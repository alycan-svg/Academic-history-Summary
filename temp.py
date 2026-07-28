     # ──────────────────────────────────────────────────────────
     # clipboard_db.py — 剪贴板内容捕获
     # 本期范围：接收数据 & 存入 SQLite
     # 后续扩展：查阅、筛选、统计
     # ──────────────────────────────────────────────────────────
    
import sqlite3
from datetime import datetime
    
# ── 数据库路径 ────────────────────────────────────────

DB_PATH = "clipboard.db"

# ── 表名 ──────────────────────────────────────────────

TABLE_NAME = "clipboard_captures"

# ── 字段定义（后续查阅/筛选都会用到） ─────────────────

FIELD_ID           = "id"
FIELD_CONTENT_TYPE = "content_type"
FIELD_CONTENT      = "content"
FIELD_SUMMARY      = "summary"
FIELD_CAPTURED_AT  = "captured_at"
FIELD_ORIGIN       = "origin"
FIELD_IS_PROCESSED = "false"
FIELD_CREATED_AT   = "created_at"
FIELD_UPDATED_AT   = "updated_at"

# 所有字段列表
ALL_FIELDS = [
    FIELD_ID,
    FIELD_CONTENT_TYPE,
    FIELD_CONTENT,
    FIELD_SUMMARY,
    FIELD_CAPTURED_AT,
    FIELD_ORIGIN,
    FIELD_IS_PROCESSED,
    FIELD_CREATED_AT,
    FIELD_UPDATED_AT,
]

# ── content_type 可选值 ───────────────────────────────

CONTENT_TYPE_URL  = "url"
CONTENT_TYPE_TEXT = "text"
VALID_CONTENT_TYPES = (CONTENT_TYPE_URL, CONTENT_TYPE_TEXT)

# ── 默认值 ────────────────────────────────────────────

DEFAULT_ORIGIN       = "clipboard"
DEFAULT_IS_PROCESSED = 0

# ── 建表 SQL（预留，供后续 init_db 模块使用）──────────

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    {FIELD_ID}            INTEGER PRIMARY KEY AUTOINCREMENT,
    {FIELD_CONTENT_TYPE}  TEXT    NOT NULL CHECK({FIELD_CONTENT_TYPE} IN ('url', 'text')),
    {FIELD_CONTENT}       TEXT    NOT NULL,
    {FIELD_SUMMARY}       TEXT,
    {FIELD_CAPTURED_AT}   TEXT    NOT NULL,
    {FIELD_ORIGIN}        TEXT    NOT NULL DEFAULT '{DEFAULT_ORIGIN}',
    {FIELD_IS_PROCESSED}  INTEGER NOT NULL DEFAULT {DEFAULT_IS_PROCESSED},
    {FIELD_CREATED_AT}    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    {FIELD_UPDATED_AT}    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

INSERT_SQL = f"""
INSERT INTO {TABLE_NAME}
    ({FIELD_CONTENT_TYPE}, {FIELD_CONTENT}, {FIELD_CAPTURED_AT}, {FIELD_ORIGIN})
VALUES (?, ?, ?, ?)
"""

# ── 索引 SQL（预留）───────────────────────────────────

INDEX_SQLS = [
    f"CREATE INDEX IF NOT EXISTS idx_type ON {TABLE_NAME}({FIELD_CONTENT_TYPE})",
    f"CREATE INDEX IF NOT EXISTS idx_processed ON {TABLE_NAME}({FIELD_IS_PROCESSED})",
    f"CREATE INDEX IF NOT EXISTS idx_captured_at ON {TABLE_NAME}({FIELD_CAPTURED_AT})",
    f"CREATE INDEX IF NOT EXISTS idx_type_proc ON {TABLE_NAME}({FIELD_CONTENT_TYPE}, {FIELD_IS_PROCESSED})",
]


# ══════════════════════════════════════════════════════════
# 本期实现：接收数据 & 存入数据库
# ══════════════════════════════════════════════════════════

class ClipboardDB:
    """剪贴板捕获记录数据库"""

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_table()

    def _ensure_table(self):
        # 建表 + 建索引
        self.conn.execute(CREATE_TABLE_SQL)
        for sql in INDEX_SQLS:
            self.conn.execute(sql)
        self.conn.commit()

    # ── 接收数据 & 存入 ──────────────────────────────

    def receive(self, data: dict) -> int:
        # 接收一条数据并存入数据库，返回新记录的 id
        content_type = data["type"]
        content      = data["content"]
        captured_at  = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        origin       = data.get("origin", DEFAULT_ORIGIN)

        # 校验 content_type
        if content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"type 必须为 {VALID_CONTENT_TYPES}，收到: {content_type!r}")

        cur = self.conn.execute(
            INSERT_SQL,
            (content_type, content, captured_at, origin),
        )
        self.conn.commit()
        return cur.lastrowid

    def close(self):
        self.conn.close()


# ── 测试入口 ─────────────────────────────────────────────

if __name__ == "__main__":
    db = ClipboardDB()

    # 模拟收到的数据（格式与你给出的 JSON 一致）
    sample_data_list = [
        {
            "type": "url",
            "content": "https://docs.python.org/3/library/sqlite3.html",
            "timestamp": "2024-07-28 10:30:00",
            "origin": "clipboard",
            "processed": False,
            "origin": "clipboard",
            "processed": False,
        },
        {
            "type": "text",
            "content": "这是一段需要保存的文本内容。",
            "timestamp": "2024-07-28 11:00:00",
            "origin": "clipboard",
            "processed": False,
        },
        {
            "type": "url",
            "content": "https://github.com/torvalds/linux",
            "timestamp": "2024-07-20 10:30:00",
            "origin": "clipboard",
            "processed": False,
        },
    ]

    for data in sample_data_list:
        record_id = db.receive(data)
        print(f"已存入 → id={record_id}, type={data['type']}, content={data['content'][:50]}...")

    db.close()