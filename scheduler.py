"""Allergen cross-contamination scheduler.

Rules (per equipment / per allergen, event based):

* A scheduled batch "deposits" its allergens on every equipment it uses;
  deposition time is the batch planned END (the residue exists when the batch
  has finished).
* Before a later batch using allergen A may use that equipment, there must be
  a cleaning record covering A, performed strictly AFTER the latest deposition
  of A and strictly BEFORE the new batch start, whose valid_until is not
  earlier than the new batch start ("cleaning still valid").
* Equipment occupancy: a batch cannot use an equipment during an interval that
  overlaps any already scheduled batch using the same equipment.
* A blocked batch is never scheduled: it deposits no residue and occupies no
  equipment. All blocking reasons are retained.
"""
import json
from datetime import datetime


class SchedulingError(Exception):
    pass


def _parse_dt(value, label):
    if value is None or str(value).strip() == "":
        raise SchedulingError(f"{label} 缺失")
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise SchedulingError(f"{label} 时间格式无效（需要 ISO 格式，如 2026-10-05T09:00）")


def compute_schedule(conn, order=None):
    """Run the greedy scheduling algorithm.

    `order` is an optional list of batch IDs giving the desired priority.
    Batches missing from the list are appended sorted by planned_start.
    Returns a plain dict result (scheduled / blocked / risks / reasons).
    """
    order = list(order or [])

    allergen_rows = conn.execute(
        "SELECT id, code, name_zh FROM allergens WHERE active = 1 ORDER BY id"
    ).fetchall()
    allergens = {r["id"]: {"id": r["id"], "code": r["code"], "name": r["name_zh"]}
                 for r in allergen_rows}

    equip_rows = conn.execute(
        "SELECT id, code, name FROM equipment WHERE active = 1 ORDER BY id"
    ).fetchall()
    equipment = {r["id"]: {"id": r["id"], "code": r["code"], "name": r["name"]}
                 for r in equip_rows}

    batch_rows = conn.execute(
        "SELECT id, name, planned_start, planned_end, notes FROM batches ORDER BY id"
    ).fetchall()
    batches = {}
    for r in batch_rows:
        aid_rows = conn.execute(
            "SELECT allergen_id FROM batch_allergens WHERE batch_id = ?", (r["id"],)
        ).fetchall()
        eid_rows = conn.execute(
            "SELECT equipment_id FROM batch_equipment WHERE batch_id = ?", (r["id"],)
        ).fetchall()
        batches[r["id"]] = {
            "id": r["id"],
            "name": r["name"],
            "planned_start": r["planned_start"],
            "planned_end": r["planned_end"],
            "notes": r["notes"],
            "allergen_ids": [x["allergen_id"] for x in aid_rows],
            "equipment_ids": [x["equipment_id"] for x in eid_rows],
        }

    clean_rows = conn.execute(
        "SELECT id, equipment_id, cleaned_at, valid_until, covers_all, method, notes "
        "FROM cleanings ORDER BY id"
    ).fetchall()
    cleanings = {}
    for r in clean_rows:
        cids = {x["allergen_id"] for x in conn.execute(
            "SELECT allergen_id FROM cleaning_allergens WHERE cleaning_id = ?", (r["id"],)
        ).fetchall()}
        try:
            c_time = _parse_dt(r["cleaned_at"], f"清洁记录 #{r['id']} 完成时间")
            c_until = _parse_dt(r["valid_until"], f"清洁记录 #{r['id']} 有效截止")
        except SchedulingError as exc:
            raise SchedulingError(f"清洁记录 #{r['id']} 数据异常，无法排程：{exc}")
        cleanings[r["id"]] = {
            "id": r["id"],
            "equipment_id": r["equipment_id"],
            "cleaned_at": r["cleaned_at"],
            "valid_until": r["valid_until"],
            "covers_all": bool(r["covers_all"]),
            "method": r["method"],
            "notes": r["notes"],
            "covered_ids": cids,
            "cleaned_dt": c_time,
            "until_dt": c_until,
        }

    # ----- parse batch times; invalid batches are blocked outright -----
    parsed = {}
    invalid_reasons = {}
    for bid, b in batches.items():
        try:
            start = _parse_dt(b["planned_start"], f"批次「{b['name']}」开始时间")
            end = _parse_dt(b["planned_end"], f"批次「{b['name']}」结束时间")
            if end <= start:
                raise SchedulingError(f"批次「{b['name']}」结束时间必须晚于开始时间")
            parsed[bid] = (start, end)
        except SchedulingError as exc:
            invalid_reasons[bid] = [str(exc)]

    # ----- priority order: requested first, remaining appended by start time -----
    remaining = [bid for bid in batches if bid not in order]

    def _sort_key(bid):
        if bid in parsed:
            return (0, parsed[bid][0], bid)
        return (1, batches[bid]["planned_start"] or "", bid)

    remaining.sort(key=_sort_key)
    priority = [bid for bid in order if bid in batches] + remaining

    # ----- per-equipment state -----
    # occupancy[equip_id] = list of (start, end, batch_id)
    occupancy = {eid: [] for eid in equipment}
    # last_deposition[equip_id][allergen_id] = (deposit_time, source_batch_id)
    last_deposit = {eid: {} for eid in equipment}

    scheduled_items = []
    blocked_items = []

    for pos, bid in enumerate(priority, start=1):
        b = batches[bid]

        if bid in invalid_reasons:
            blocked_items.append(_blocked_view(b, pos, invalid_reasons[bid], allergens, equipment))
            continue

        start, end = parsed[bid]
        reasons = []

        # missing references
        missing_equips = [eid for eid in b["equipment_ids"] if eid not in equipment]
        if missing_equips:
            reasons.append({"code": "equipment_missing",
                            "message": f"引用了 {len(missing_equips)} 个不存在或已停用的器具"})
        unknown_allergens = [aid for aid in b["allergen_ids"] if aid not in allergens]
        if unknown_allergens:
            reasons.append({"code": "allergen_missing",
                            "message": f"引用了 {len(unknown_allergens)} 个不存在或已停用的过敏原"})
        if not b["equipment_ids"]:
            reasons.append({"code": "equipment_none", "message": "未指定任何台面/器具，无法排入"})

        # ----- 1) equipment occupancy conflicts -----
        conflict_names = []
        conflict_details = []
        for eid in b["equipment_ids"]:
            if eid not in equipment:
                continue
            for (s2, e2, other_bid) in occupancy[eid]:
                # half-open intervals may touch; overlap requires strict intersection
                if start < e2 and s2 < end:
                    name = equipment[eid]["name"]
                    other = batches[other_bid]["name"]
                    conflict_names.append(name)
                    conflict_details.append(
                        f"器具「{name}」与已排入批次「{other}」"
                        f"({s2.strftime('%H:%M')}-{e2.strftime('%H:%M')}) 时段重叠"
                    )
        if conflict_details:
            reasons.append({
                "code": "time_conflict",
                "message": "器具占用冲突：" + "；".join(sorted(set(conflict_details))),
                "equipment": sorted(set(conflict_names)),
            })

        # ----- 2) allergen residue / cleaning checks -----
        controlled_risks = []
        missing_clean_details = []
        expired_clean_details = []

        for eid in b["equipment_ids"]:
            if eid not in equipment:
                continue
            for aid in b["allergen_ids"]:
                if aid not in allergens:
                    continue
                deposit = last_deposit[eid].get(aid)
                if deposit is None:
                    continue  # no prior residue of this allergen on this equipment
                deposit_time, source_bid = deposit

                # candidate cleanings: this equipment, after deposition, before start,
                # and covering this allergen (explicit or covers_all snapshot)
                candidates = []
                for c in cleanings.values():
                    if c["equipment_id"] != eid:
                        continue
                    if not (c["covers_all"] or aid in c["covered_ids"]):
                        continue
                    c_time, c_until = c["cleaned_dt"], c["until_dt"]
                    if not (c_time > deposit_time and c_time < start):
                        continue
                    candidates.append((c_time, c_until, c))

                equip_name = equipment[eid]["name"]
                al = allergens[aid]["name"]
                source_name = batches[source_bid]["name"]
                prefix = (f"器具「{equip_name}」/ 过敏原「{al}」："
                          f"残留来自已排入批次「{source_name}」"
                          f"({deposit_time.strftime('%m-%d %H:%M')} 结束后沉积)")

                if not candidates:
                    msg = f"{prefix}，在其之后、本批次开始之前没有覆盖该过敏原的清洁记录"
                    missing_clean_details.append(msg)
                    reasons.append({
                        "code": "missing_cleaning",
                        "message": msg,
                        "equipment_id": eid,
                        "allergen_id": aid,
                        "source_batch_id": source_bid,
                    })
                    continue

                # latest applicable cleaning decides the status of the surface
                candidates.sort(key=lambda x: (x[0], x[2]["id"]))
                _, c_until, c_latest = candidates[-1]
                if c_until < start:
                    msg = (f"{prefix}，最近清洁 #{c_latest['id']}"
                           f"({c_latest['cleaned_at'][11:16]}) "
                           f"已于 {c_latest['valid_until'][5:16].replace('T', ' ')} 失效，"
                           f"早于本批次开始 {start.strftime('%m-%d %H:%M')}")
                    expired_clean_details.append(msg)
                    reasons.append({
                        "code": "cleaning_expired",
                        "message": msg,
                        "equipment_id": eid,
                        "allergen_id": aid,
                        "source_batch_id": source_bid,
                        "cleaning_id": c_latest["id"],
                    })
                else:
                    # controlled: legally scheduleable, but the risk source is recorded
                    controlled_risks.append({
                        "equipment_id": eid,
                        "equipment": equip_name,
                        "allergen_id": aid,
                        "allergen": al,
                        "source_batch_id": source_bid,
                        "source_batch": source_name,
                        "deposited_at": deposit_time.isoformat(),
                        "cleaning_id": c_latest["id"],
                        "cleaned_at": c_latest["cleaned_at"],
                        "valid_until": c_latest["valid_until"],
                        "method": c_latest["method"],
                        "message": f"{prefix}；清洁 #{c_latest['id']} 覆盖该过敏原，"
                                   f"有效期至 {c_latest['valid_until'][5:16].replace('T', ' ')}，"
                                   f"本批次开始时仍有效",
                    })

        if reasons:
            blocked_items.append(_blocked_view(b, pos, reasons, allergens, equipment,
                                               risks=controlled_risks))
            # blocked: deposits nothing, occupies nothing
            continue

        # ----- schedule it -----
        for eid in b["equipment_ids"]:
            if eid in equipment:
                occupancy[eid].append((start, end, bid))
        for eid in b["equipment_ids"]:
            if eid not in equipment:
                continue
            for aid in b["allergen_ids"]:
                if aid not in allergens:
                    continue
                last_deposit[eid][aid] = (end, bid)

        controlled_risks.sort(key=lambda r: (r["equipment"], r["allergen"]))
        scheduled_items.append({
            "position": pos,
            "batch_id": bid,
            "name": b["name"],
            "planned_start": b["planned_start"],
            "planned_end": b["planned_end"],
            "notes": b["notes"],
            "allergens": [allergens[a]["name"] for a in b["allergen_ids"] if a in allergens],
            "equipment": [equipment[e]["name"] for e in b["equipment_ids"] if e in equipment],
            "risk_level": "controlled" if controlled_risks else "clean",
            "risks": controlled_risks,
        })

    blocked_items.sort(key=lambda x: x["position"])
    return {
        "order": priority,
        "scheduled": scheduled_items,
        "blocked": blocked_items,
        "summary": {
            "total": len(priority),
            "scheduled_count": len(scheduled_items),
            "blocked_count": len(blocked_items),
            "controlled_risk_count": sum(len(x["risks"]) for x in scheduled_items),
        },
    }


def _blocked_view(batch, position, reasons, allergens, equipment, risks=None):
    return {
        "position": position,
        "batch_id": batch["id"],
        "name": batch["name"],
        "planned_start": batch["planned_start"],
        "planned_end": batch["planned_end"],
        "notes": batch["notes"],
        "allergens": [allergens[a]["name"] for a in batch["allergen_ids"] if a in allergens],
        "equipment": [equipment[e]["name"] for e in batch["equipment_ids"] if e in equipment],
        "status": "blocked",
        "reasons": reasons,
        "risks": risks or [],
    }


# ----------------------------- persistence -----------------------------

def save_run(conn, result):
    cur = conn.execute(
        "INSERT INTO schedule_runs(generated_at, order_json) VALUES (?, ?)",
        (datetime.now().replace(microsecond=0).isoformat(), json.dumps(result["order"])),
    )
    run_id = cur.lastrowid
    for item in result["scheduled"]:
        conn.execute(
            "INSERT INTO schedule_run_items(run_id, position, batch_id, status, risk_json, reasons_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, item["position"], item["batch_id"], "scheduled",
             json.dumps(item["risks"], ensure_ascii=False), "[]"),
        )
    for item in result["blocked"]:
        conn.execute(
            "INSERT INTO schedule_run_items(run_id, position, batch_id, status, risk_json, reasons_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, item["position"], item["batch_id"], "blocked",
             json.dumps(item.get("risks", []), ensure_ascii=False),
             json.dumps(item["reasons"], ensure_ascii=False)),
        )
    conn.commit()
    return run_id


def load_latest_run(conn):
    run = conn.execute(
        "SELECT id, generated_at, order_json FROM schedule_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if run is None:
        return None
    items = conn.execute(
        "SELECT position, batch_id, status, risk_json, reasons_json "
        "FROM schedule_run_items WHERE run_id = ? ORDER BY position", (run["id"],)
    ).fetchall()

    batch_cache = {}

    def _batch_view(bid):
        if bid in batch_cache:
            return batch_cache[bid]
        row = conn.execute(
            "SELECT id, name, planned_start, planned_end, notes FROM batches WHERE id = ?", (bid,)
        ).fetchone()
        if row is None:
            batch_cache[bid] = None
            return None
        als = [r["name_zh"] for r in conn.execute(
            "SELECT a.name_zh FROM batch_allergens ba JOIN allergens a ON a.id = ba.allergen_id "
            "WHERE ba.batch_id = ? ORDER BY a.id", (bid,))]
        eqs = [r["name"] for r in conn.execute(
            "SELECT e.name FROM batch_equipment be JOIN equipment e ON e.id = be.equipment_id "
            "WHERE be.batch_id = ? ORDER BY e.id", (bid,))]
        view = {
            "batch_id": row["id"], "name": row["name"],
            "planned_start": row["planned_start"], "planned_end": row["planned_end"],
            "notes": row["notes"], "allergens": als, "equipment": eqs,
        }
        batch_cache[bid] = view
        return view

    scheduled, blocked = [], []
    controlled = 0
    for it in items:
        view = _batch_view(it["batch_id"])
        if view is None:
            continue
        risks = json.loads(it["risk_json"] or "[]")
        entry = {"position": it["position"], **view}
        if it["status"] == "scheduled":
            entry["risk_level"] = "controlled" if risks else "clean"
            entry["risks"] = risks
            controlled += len(risks)
            scheduled.append(entry)
        else:
            entry["status"] = "blocked"
            entry["reasons"] = json.loads(it["reasons_json"] or "[]")
            entry["risks"] = risks
            blocked.append(entry)

    return {
        "run_id": run["id"],
        "generated_at": run["generated_at"],
        "order": json.loads(run["order_json"] or "[]"),
        "scheduled": scheduled,
        "blocked": blocked,
        "summary": {
            "total": len(scheduled) + len(blocked),
            "scheduled_count": len(scheduled),
            "blocked_count": len(blocked),
            "controlled_risk_count": controlled,
        },
    }


# ------------------------------- export -------------------------------

def export_csv(result):
    import io
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["顺序", "状态", "批次", "开始", "结束", "台面/器具", "过敏原",
                "风险/阻塞说明"])
    for item in result["scheduled"]:
        notes = [r["message"] for r in item["risks"]]
        w.writerow([item["position"], "可执行", item["name"],
                    item["planned_start"], item["planned_end"],
                    "|".join(item["equipment"]), "|".join(item["allergens"]),
                    " || ".join(notes)])
    for item in result["blocked"]:
        notes = [r["message"] for r in item["reasons"]]
        w.writerow([item["position"], "已阻塞", item["name"],
                    item["planned_start"], item["planned_end"],
                    "|".join(item["equipment"]), "|".join(item["allergens"]),
                    " || ".join(notes)])
    return buf.getvalue()


def export_markdown(result, generated_at=None):
    lines = [
        "# 共享厨房过敏原交叉污染排程表",
        "",
        f"- 生成时间：{generated_at or datetime.now().replace(microsecond=0).isoformat()}",
        f"- 批次总数：{result['summary']['total']}；"
        f"可执行：{result['summary']['scheduled_count']}；"
        f"阻塞：{result['summary']['blocked_count']}；"
        f"受控残留风险点：{result['summary']['controlled_risk_count']}",
        "",
        "## 可执行顺序",
        "",
        "| 顺序 | 批次 | 时段 | 台面/器具 | 过敏原 | 风险来源 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in result["scheduled"]:
        risks = "<br>".join(
            f"⚠ {r['message']}" for r in item["risks"]
        ) or "✓ 无前序残留"
        lines.append(
            f"| {item['position']} | {item['name']} | "
            f"{item['planned_start']} ~ {item['planned_end']} | "
            f"{', '.join(item['equipment'])} | {', '.join(item['allergens'])} | {risks} |"
        )
    lines += ["", "## 被阻塞批次及原因", ""]
    if not result["blocked"]:
        lines.append("无。")
    for item in result["blocked"]:
        lines.append(f"### {item['position']}. {item['name']}（{item['planned_start']} ~ {item['planned_end']}）")
        lines.append("")
        for r in item["reasons"]:
            lines.append(f"- 🚫 {r['message']}")
        for r in item.get("risks", []):
            lines.append(f"- ⚠（已受控）{r['message']}")
        lines.append("")
    return "\n".join(lines)
