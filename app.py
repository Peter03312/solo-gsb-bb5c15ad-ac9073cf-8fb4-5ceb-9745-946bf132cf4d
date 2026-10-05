"""Shared-kitchen allergen scheduling web app.

Zero third-party dependencies for application code: a small WSGI router over
Python's standard library. In production gunicorn serves `application`;
for local development run `python3 app.py` (wsgiref).
"""
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from db import get_conn, init_db, now_iso
import scheduler
import seed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

PORT = int(os.environ.get("PORT", "8000"))
PUBLIC_URL = os.environ.get("PUBLIC_URL", f"http://localhost:{PORT}")


# ------------------------------- helpers -------------------------------

def _json_response(start_response, payload, status="200 OK", headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = [("Content-Type", "application/json; charset=utf-8"),
         ("Content-Length", str(len(body)))]
    if headers:
        h += headers
    start_response(status, h)
    return [body]


def _error(start_response, message, code=400):
    status = f"{code} {_REASONS.get(code, 'Error')}"
    return _json_response(start_response, {"error": message}, status)


_REASONS = {400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
            409: "Conflict", 500: "Internal Server Error"}


def _read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    raw = environ["wsgi.input"].read(length) if length else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("请求体不是合法 JSON")
    if not isinstance(data, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return data


def _dt(value, label):
    if value is None or str(value).strip() == "":
        raise ValueError(f"{label}不能为空")
    text = str(value).strip()
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{label}格式无效，应为 YYYY-MM-DDTHH:MM（如 2026-10-05T09:00）")


def _require(data, key, label):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{label}不能为空")
    return value


def _unique_code(conn, table, code):
    """Ensure uniqueness against an existing code column."""
    base, n = code, 2
    while conn.execute(f"SELECT 1 FROM {table} WHERE code = ?", (code,)).fetchone():
        code = f"{base}_{n}"
        n += 1
    return code


def _id_list(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("过敏原/器具参数必须是数组")
    out = []
    for x in value:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            raise ValueError("过敏原/器具 ID 必须为整数")
    return out


# --------------------------- resource views ---------------------------

def _batch_views(conn):
    out = []
    for r in conn.execute(
        "SELECT b.id, b.name, b.planned_start, b.planned_end, b.notes "
        "FROM batches b ORDER BY b.planned_start, b.id"
    ).fetchall():
        als = [{"id": x["id"], "name": x["name_zh"]} for x in conn.execute(
            "SELECT a.id, a.name_zh FROM batch_allergens ba "
            "JOIN allergens a ON a.id = ba.allergen_id WHERE ba.batch_id = ? ORDER BY a.id",
            (r["id"],))]
        eqs = [{"id": x["id"], "name": x["name"]} for x in conn.execute(
            "SELECT e.id, e.name FROM batch_equipment be "
            "JOIN equipment e ON e.id = be.equipment_id WHERE be.batch_id = ? ORDER BY e.id",
            (r["id"],))]
        out.append({"id": r["id"], "name": r["name"],
                    "planned_start": r["planned_start"], "planned_end": r["planned_end"],
                    "notes": r["notes"],
                    "allergens": als, "equipment": eqs})
    return out


def _cleaning_views(conn):
    out = []
    for r in conn.execute(
        "SELECT c.id, c.equipment_id, e.name AS equipment_name, c.cleaned_at, "
        "c.valid_until, c.covers_all, c.method, c.notes "
        "FROM cleanings c JOIN equipment e ON e.id = c.equipment_id ORDER BY c.cleaned_at, c.id"
    ).fetchall():
        als = [{"id": x["id"], "name": x["name_zh"]} for x in conn.execute(
            "SELECT a.id, a.name_zh FROM cleaning_allergens ca "
            "JOIN allergens a ON a.id = ca.allergen_id WHERE ca.cleaning_id = ? ORDER BY a.id",
            (r["id"],))]
        now = datetime.now()
        try:
            valid = datetime.fromisoformat(r["valid_until"]) >= now
        except ValueError:
            valid = False
        out.append({"id": r["id"], "equipment_id": r["equipment_id"],
                    "equipment_name": r["equipment_name"],
                    "cleaned_at": r["cleaned_at"], "valid_until": r["valid_until"],
                    "covers_all": bool(r["covers_all"]), "method": r["method"],
                    "notes": r["notes"], "allergens": als, "currently_valid": valid})
    return out


# ------------------------------- routes -------------------------------

def route(environ, start_response):
    method = environ["REQUEST_METHOD"]
    path = environ.get("PATH_INFO", "/")
    query = parse_qs(environ.get("QUERY_STRING", ""))

    # ---- static ----
    if path == "/":
        return _serve_static(start_response, "index.html")
    if path.startswith("/static/"):
        return _serve_static(start_response, path[len("/static/"):])

    # ---- meta ----
    if path == "/api/meta" and method == "GET":
        return _json_response(start_response, {
            "public_url": PUBLIC_URL, "port": PORT,
            "server_time": now_iso(),
        })

    # ---- allergens ----
    if path == "/api/allergens":
        with get_conn() as conn:
            if method == "GET":
                rows = conn.execute(
                    "SELECT id, code, name_zh, name_en, is_builtin FROM allergens "
                    "WHERE active = 1 ORDER BY is_builtin DESC, id").fetchall()
                return _json_response(start_response, [dict(r) for r in rows])
            if method == "POST":
                try:
                    data = _read_json(environ)
                    name = str(_require(data, "name_zh", "过敏原名称")).strip()
                    code = re.sub(r"[^a-z0-9_]+", "_",
                                  str(data.get("code") or name).lower()).strip("_")
                    if not code:
                        code = f"custom_{abs(hash(name)) % 1000000:06d}"
                    code = _unique_code(conn, "allergens", code)
                    cur = conn.execute(
                        "INSERT INTO allergens(code, name_zh, name_en, is_builtin, created_at) "
                        "VALUES (?, ?, ?, 0, ?)",
                        (code, name, str(data.get("name_en", "")), now_iso()))
                    conn.commit()
                    return _json_response(start_response, {"id": cur.lastrowid}, "201 Created")
                except ValueError as exc:
                    return _error(start_response, str(exc))
                except Exception as exc:  # unique code etc.
                    return _error(start_response, f"保存失败：{exc}", 409)

    # ---- equipment ----
    if path == "/api/equipment":
        with get_conn() as conn:
            if method == "GET":
                rows = conn.execute(
                    "SELECT id, code, name, equip_type FROM equipment "
                    "WHERE active = 1 ORDER BY id").fetchall()
                return _json_response(start_response, [dict(r) for r in rows])
            if method == "POST":
                try:
                    data = _read_json(environ)
                    name = str(_require(data, "name", "器具名称")).strip()
                    code = re.sub(r"[^a-zA-Z0-9_]+", "_", str(data.get("code") or name)).strip("_")
                    if not code:
                        code = f"equip_{abs(hash(name)) % 1000000:06d}"
                    code = _unique_code(conn, "equipment", code)
                    cur = conn.execute(
                        "INSERT INTO equipment(code, name, equip_type, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (code, name, str(data.get("equip_type", "")), now_iso()))
                    conn.commit()
                    return _json_response(start_response, {"id": cur.lastrowid}, "201 Created")
                except ValueError as exc:
                    return _error(start_response, str(exc))
                except Exception as exc:
                    return _error(start_response, f"保存失败：{exc}", 409)

    # ---- batches ----
    m = re.fullmatch(r"/api/batches(?:/(\d+))?", path)
    if m:
        with get_conn() as conn:
            bid = m.group(1)
            if bid:
                bid = int(bid)
                if method == "DELETE":
                    cur = conn.execute("DELETE FROM batches WHERE id = ?", (bid,))
                    conn.commit()
                    if cur.rowcount == 0:
                        return _error(start_response, "批次不存在", 404)
                    return _json_response(start_response, {"deleted": bid})
                return _error(start_response, "仅支持 DELETE", 405)
            if method == "GET":
                return _json_response(start_response, _batch_views(conn))
            if method == "POST":
                try:
                    data = _read_json(environ)
                    name = str(_require(data, "name", "批次名称")).strip()
                    start = _dt(_require(data, "planned_start", "开始时间"), "开始时间")
                    end = _dt(_require(data, "planned_end", "结束时间"), "结束时间")
                    if end <= start:
                        raise ValueError("结束时间必须晚于开始时间")
                    aids = _id_list(data.get("allergen_ids"))
                    eids = _id_list(data.get("equipment_ids"))
                    if not eids:
                        raise ValueError("请至少选择一个台面/器具")
                    bad_a = [a for a in aids if not conn.execute(
                        "SELECT 1 FROM allergens WHERE id = ? AND active = 1", (a,)).fetchone()]
                    if bad_a:
                        raise ValueError("包含不存在的过敏原 ID: %s" % bad_a)
                    bad_e = [e for e in eids if not conn.execute(
                        "SELECT 1 FROM equipment WHERE id = ? AND active = 1", (e,)).fetchone()]
                    if bad_e:
                        raise ValueError("包含不存在的器具 ID: %s" % bad_e)
                    cur = conn.execute(
                        "INSERT INTO batches(name, planned_start, planned_end, notes, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (name, start.isoformat(), end.isoformat(),
                         str(data.get("notes", "")), now_iso()))
                    bid_new = cur.lastrowid
                    conn.executemany(
                        "INSERT INTO batch_allergens(batch_id, allergen_id) VALUES (?, ?)",
                        [(bid_new, a) for a in dict.fromkeys(aids)])
                    conn.executemany(
                        "INSERT INTO batch_equipment(batch_id, equipment_id) VALUES (?, ?)",
                        [(bid_new, e) for e in dict.fromkeys(eids)])
                    conn.commit()
                    return _json_response(start_response, {"id": bid_new}, "201 Created")
                except ValueError as exc:
                    return _error(start_response, str(exc))
            return _error(start_response, "仅支持 GET/POST/DELETE", 405)

    # ---- cleanings ----
    m = re.fullmatch(r"/api/cleanings(?:/(\d+))?", path)
    if m:
        with get_conn() as conn:
            cid = m.group(1)
            if cid:
                cid = int(cid)
                if method == "DELETE":
                    cur = conn.execute("DELETE FROM cleanings WHERE id = ?", (cid,))
                    conn.commit()
                    if cur.rowcount == 0:
                        return _error(start_response, "清洁记录不存在", 404)
                    return _json_response(start_response, {"deleted": cid})
                return _error(start_response, "仅支持 DELETE", 405)
            if method == "GET":
                return _json_response(start_response, _cleaning_views(conn))
            if method == "POST":
                try:
                    data = _read_json(environ)
                    eid = int(_require(data, "equipment_id", "台面/器具"))
                    if not conn.execute("SELECT 1 FROM equipment WHERE id = ?", (eid,)).fetchone():
                        raise ValueError("台面/器具不存在")
                    cleaned = _dt(_require(data, "cleaned_at", "清洁完成时间"), "清洁完成时间")
                    until = _dt(_require(data, "valid_until", "有效截止时间"), "有效截止时间")
                    if until <= cleaned:
                        raise ValueError("有效截止时间必须晚于清洁完成时间")
                    covers_all = bool(data.get("covers_all"))
                    aids = [] if covers_all else _id_list(data.get("allergen_ids"))
                    if not covers_all and not aids:
                        raise ValueError("未“覆盖全部过敏原”时，必须勾选至少一种被清除的过敏原")
                    bad_a = [a for a in aids if not conn.execute(
                        "SELECT 1 FROM allergens WHERE id = ? AND active = 1", (a,)).fetchone()]
                    if bad_a:
                        raise ValueError("包含不存在的过敏原 ID: %s" % bad_a)
                    cur = conn.execute(
                        "INSERT INTO cleanings(equipment_id, cleaned_at, valid_until, covers_all, "
                        "method, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (eid, cleaned.isoformat(), until.isoformat(), 1 if covers_all else 0,
                         str(data.get("method", "")), str(data.get("notes", "")), now_iso()))
                    cid_new = cur.lastrowid
                    conn.executemany(
                        "INSERT INTO cleaning_allergens(cleaning_id, allergen_id) VALUES (?, ?)",
                        [(cid_new, a) for a in dict.fromkeys(aids)])
                    conn.commit()
                    return _json_response(start_response, {"id": cid_new}, "201 Created")
                except ValueError as exc:
                    return _error(start_response, str(exc))
            return _error(start_response, "仅支持 GET/POST/DELETE", 405)

    # ---- schedule ----
    if path == "/api/schedule/run" and method == "POST":
        try:
            data = _read_json(environ)
            order = data.get("order") or []
            if not isinstance(order, list):
                raise ValueError("order 必须是批次 ID 数组")
            order = [int(x) for x in order]
        except ValueError as exc:
            return _error(start_response, str(exc))
        with get_conn() as conn:
            try:
                result = scheduler.compute_schedule(conn, order=order)
                result["run_id"] = scheduler.save_run(conn, result)
                result["generated_at"] = now_iso()
                return _json_response(start_response, result, "201 Created")
            except scheduler.SchedulingError as exc:
                return _error(start_response, str(exc))

    if path == "/api/schedule/latest" and method == "GET":
        with get_conn() as conn:
            result = scheduler.load_latest_run(conn)
            if result is None:
                return _json_response(start_response, None)
            return _json_response(start_response, result)

    if path == "/api/schedule/export" and method == "GET":
        fmt = query.get("format", ["csv"])[0]
        with get_conn() as conn:
            result = scheduler.load_latest_run(conn)
            if result is None:
                fresh = scheduler.compute_schedule(conn)
                run_id = scheduler.save_run(conn, fresh)
                result = scheduler.load_latest_run(conn)
                result["run_id"] = run_id
            generated_at = result.get("generated_at")
            if fmt == "csv":
                body = scheduler.export_csv(result).encode("utf-8-sig")
                ctype = "text/csv; charset=utf-8"
                fname = "schedule.csv"
            elif fmt in ("md", "markdown"):
                body = scheduler.export_markdown(result, generated_at).encode("utf-8")
                ctype = "text/markdown; charset=utf-8"
                fname = "schedule.md"
            else:
                return _error(start_response, "format 仅支持 csv 或 md")
            start_response("200 OK", [
                ("Content-Type", ctype),
                ("Content-Disposition", f'attachment; filename="{fname}"'),
                ("Content-Length", str(len(body))),
            ])
            return [body]

    # ---- admin ----
    if path == "/api/admin/seed-demo" and method == "POST":
        with get_conn() as conn:
            seed.load_demo(conn)
        return _json_response(start_response, {"ok": True})

    return _error(start_response, f"未找到路径：{method} {path}", 404)


# --------------------------- static serving ---------------------------

_MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".png": "image/png", ".ico": "image/x-icon"}


def _serve_static(start_response, rel):
    rel = rel.lstrip("/")
    full = os.path.normpath(os.path.join(STATIC_DIR, rel))
    if not full.startswith(STATIC_DIR + os.sep) and full != STATIC_DIR:
        return _error(start_response, "非法路径", 400)
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    if not os.path.isfile(full):
        return _error(start_response, "文件不存在", 404)
    try:
        with open(full, "rb") as fh:
            body = fh.read()
    except OSError:
        return _error(start_response, "文件读取失败", 500)
    ext = os.path.splitext(full)[1]
    start_response("200 OK", [("Content-Type", _MIME.get(ext, "application/octet-stream")),
                              ("Content-Length", str(len(body))),
                              ("Cache-Control", "no-cache")])
    return [body]


def application(environ, start_response):
    try:
        return route(environ, start_response)
    except Exception as exc:  # pragma: no cover - last resort
        tb = __import__("traceback").format_exc()
        sys.stderr.write(tb + "\n")
        return _error(start_response, f"服务器内部错误：{exc}", 500)


if __name__ == "__main__":
    init_db()
    httpd = make_server("0.0.0.0", PORT, application)
    print(f" * Shared-kitchen scheduler running on http://0.0.0.0:{PORT}")
    print(f" * Public URL shown in page: {PUBLIC_URL}")
    httpd.serve_forever()
