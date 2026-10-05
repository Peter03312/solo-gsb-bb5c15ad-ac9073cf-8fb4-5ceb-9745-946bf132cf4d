"""Idempotent demo dataset: covers controlled-risk, missing-cleaning,
expired-cleaning and time-conflict cases on a single production day."""
from db import now_iso


def _allergen_ids(conn):
    rows = conn.execute("SELECT id, code FROM allergens").fetchall()
    return {r["code"]: r["id"] for r in rows}


def load_demo(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    # reset business data, keep built-in allergens
    conn.executescript(
        "DELETE FROM schedule_run_items; DELETE FROM schedule_runs; "
        "DELETE FROM cleaning_allergens; DELETE FROM cleanings; "
        "DELETE FROM batch_allergens; DELETE FROM batch_equipment; "
        "DELETE FROM batches; DELETE FROM equipment; "
        "DELETE FROM allergens WHERE is_builtin = 0;"
    )
    ts = now_iso()

    equipment = [
        ("bench-1", "中岛台面", "操作台"),
        ("bench-2", "烘焙台面", "操作台"),
        ("oven-1", "层式烤箱", "烤箱"),
        ("mixer-1", "立式搅拌机", "搅拌器具"),
        ("wok-1", "猛火炒灶", "炉灶"),
        ("proofer-1", "冷藏醒发箱", "醒发设备"),
    ]
    equip_ids = {}
    for code, name, etype in equipment:
        cur = conn.execute(
            "INSERT INTO equipment(code, name, equip_type, created_at) VALUES (?, ?, ?, ?)",
            (code, name, etype, ts))
        equip_ids[code] = cur.lastrowid

    al = _allergen_ids(conn)

    def _batch(name, s, e, codes, equip_codes, notes=""):
        cur = conn.execute(
            "INSERT INTO batches(name, planned_start, planned_end, notes, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (name, s, e, notes, ts))
        bid = cur.lastrowid
        for c in codes:
            conn.execute("INSERT INTO batch_allergens(batch_id, allergen_id) VALUES (?, ?)",
                         (bid, al[c]))
        for ec in equip_codes:
            conn.execute("INSERT INTO batch_equipment(batch_id, equipment_id) VALUES (?, ?)",
                         (bid, equip_ids[ec]))
        return bid

    D = "2026-10-05"
    # 1. 早餐档：含乳/蛋/麸质
    _batch("奶香餐包", f"{D}T08:00", f"{D}T09:00",
           ["milk", "eggs", "gluten"], ["bench-2", "mixer-1", "oven-1"],
           "早餐档，使用共享搅拌机与烤箱")
    # 2. 热菜档：甲壳类（台面/炒灶首次使用该过敏原）
    _batch("虾仁时蔬", f"{D}T09:00", f"{D}T09:45",
           ["crustaceans"], ["bench-1", "wok-1"])
    # 3. 插队单：与虾仁时蔬抢同一台面和炒灶 -> 时段冲突
    _batch("加急蛋炒饭", f"{D}T09:15", f"{D}T10:00",
           ["eggs"], ["bench-1", "wok-1"], "临时加单")
    # 4. 烘焙转档：切换前对烘焙台面/搅拌机/烤箱做了 4 小时有效清洁 -> 受控风险
    _batch("全麦贝果", f"{D}T09:30", f"{D}T10:30",
           ["gluten", "sesame"], ["bench-2", "mixer-1", "oven-1", "proofer-1"])
    # 5. 含坚果玛芬：搅拌机/烤箱上的乳蛋残留由 C1/C3 清洁覆盖，坚果为首次 -> 可排入
    _batch("坚果玛芬", f"{D}T10:30", f"{D}T11:30",
           ["milk", "eggs", "nuts"], ["mixer-1", "oven-1"])
    # 6. 花生曲奇：烘焙台面清洁仅 10 分钟有效（已过期）；
    #    醒发箱的清洁只清除乳/蛋、未覆盖麸质（覆盖不全=缺清洁）-> 两类阻塞同时出现
    _batch("花生曲奇", f"{D}T11:00", f"{D}T12:00",
           ["peanuts", "gluten"], ["bench-2", "proofer-1"])

    def _cleaning(equip_code, at, until, codes, covers_all=False, method=""):
        cur = conn.execute(
            "INSERT INTO cleanings(equipment_id, cleaned_at, valid_until, covers_all, "
            "method, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (equip_ids[equip_code], at, until, 1 if covers_all else 0, method,
             "演示数据", ts))
        cid = cur.lastrowid
        for c in codes:
            conn.execute("INSERT INTO cleaning_allergens(cleaning_id, allergen_id) VALUES (?, ?)",
                         (cid, al[c]))

    hot_wash = "热碱水清洗 + 清水冲洗"
    _cleaning("bench-2", f"{D}T09:10", f"{D}T13:10",
              ["milk", "eggs", "gluten"], method=hot_wash)
    _cleaning("mixer-1", f"{D}T09:10", f"{D}T13:10",
              ["milk", "eggs", "gluten"], method="拆卸搅拌头后洗碗机高温程序")
    _cleaning("oven-1", f"{D}T09:05", f"{D}T13:05",
              ["milk", "eggs", "gluten"], method="高温空烤+内壁擦拭")
    # 醒发箱只对乳/蛋做了清洁：刻意未覆盖麸质，演示“清洁未覆盖全部残留源”
    _cleaning("proofer-1", f"{D}T09:05", f"{D}T13:05",
              ["milk", "eggs"], method="擦拭内壁")
    # 仅 10 分钟有效的清洁：用于演示“清洁失效”
    _cleaning("bench-2", f"{D}T10:40", f"{D}T10:50",
              ["peanuts", "gluten"], method="普通抹布擦拭（演示：有效期过短）")

    conn.commit()
