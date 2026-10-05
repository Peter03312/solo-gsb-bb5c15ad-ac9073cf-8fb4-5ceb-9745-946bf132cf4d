"""SQLite connection, schema initialization and seed allergens."""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "kitchen.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS allergens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    name_zh    TEXT NOT NULL,
    name_en    TEXT NOT NULL DEFAULT '',
    is_builtin INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS equipment (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    equip_type TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    planned_start TEXT NOT NULL,
    planned_end   TEXT NOT NULL,
    notes         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch_allergens (
    batch_id    INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    allergen_id INTEGER NOT NULL REFERENCES allergens(id) ON DELETE CASCADE,
    PRIMARY KEY (batch_id, allergen_id)
);
CREATE TABLE IF NOT EXISTS batch_equipment (
    batch_id    INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    PRIMARY KEY (batch_id, equipment_id)
);
CREATE TABLE IF NOT EXISTS cleanings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
    cleaned_at   TEXT NOT NULL,
    valid_until  TEXT NOT NULL,
    covers_all   INTEGER NOT NULL DEFAULT 0,
    method       TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cleaning_allergens (
    cleaning_id INTEGER NOT NULL REFERENCES cleanings(id) ON DELETE CASCADE,
    allergen_id INTEGER NOT NULL REFERENCES allergens(id) ON DELETE CASCADE,
    PRIMARY KEY (cleaning_id, allergen_id)
);
CREATE TABLE IF NOT EXISTS schedule_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at TEXT NOT NULL,
    order_json   TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS schedule_run_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL REFERENCES schedule_runs(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    batch_id  INTEGER,
    status    TEXT NOT NULL,
    risk_json TEXT NOT NULL DEFAULT '[]',
    reasons_json TEXT NOT NULL DEFAULT '[]'
);
"""

# GB 7718 / Codex 常见致敏物清单
BUILTIN_ALLERGENS = [
    ("gluten", "含麸质谷物", "Cereals containing gluten"),
    ("crustaceans", "甲壳纲动物", "Crustaceans"),
    ("eggs", "蛋", "Eggs"),
    ("fish", "鱼", "Fish"),
    ("peanuts", "花生", "Peanuts"),
    ("soy", "大豆", "Soybeans"),
    ("milk", "乳及乳制品", "Milk"),
    ("nuts", "坚果(树坚果)", "Tree nuts"),
    ("celery", "芹菜", "Celery"),
    ("mustard", "芥末", "Mustard"),
    ("sesame", "芝麻", "Sesame"),
    ("sulphites", "亚硫酸盐(≥10mg/kg)", "Sulphites"),
]


def get_conn(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def init_db(conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.executescript(SCHEMA)
        ts = now_iso()
        for code, zh, en in BUILTIN_ALLERGENS:
            conn.execute(
                "INSERT OR IGNORE INTO allergens(code, name_zh, name_en, is_builtin, active, created_at) "
                "VALUES (?, ?, ?, 1, 1, ?)",
                (code, zh, en, ts),
            )
        conn.commit()
    finally:
        if own:
            conn.close()
