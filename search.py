
# ====================================================================
# 剪贴板数据库 — 一个能存能查的小工具
#
# 它能做什么？
#   1. 把剪贴板内容和浏览器历史存进 SQLite 数据库
#   2. 用关键词、类型、来源、时间等条件查询已存的数据
#   3. 预留了 AI 接口，以后可以用自然语言提问来搜索
# ====================================================================

import json
import re
import sqlite3
from datetime import datetime

import requests  # 如果报错 No module named 'requests'，在终端运行: pip install requests

# ============================================================
# DeepSeek API 配置 — 密钥存在 config.py 中，不会提交到 git
# 如果你还没有 config.py，复制 config.example.py 并填入你的 Key
# 获取地址: https://platform.deepseek.com/api_keys
# ============================================================

try:
    from config import DEEPSEEK_API_KEY
except ImportError:
    DEEPSEEK_API_KEY = "sk-你的API-Key填在这里"  # 未配置 config.py 时的占位符

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# ============================================================
# 第一部分：数据库和表的配置
# 这里集中定义了所有"叫什么"、"有哪些可选值"，改一处全文件生效
# ============================================================

# 数据库文件就放在当前目录下
DB_PATH = "clipboard.db"

# 表名 — 所有数据存在这一张表里
TABLE_NAME = "clipboard_captures"

# 表中每一列的名称（用常量避免硬编码字符串，不容易写错）
FIELD_ID           = "id"              # 自增主键，每条记录的唯一编号
FIELD_CONTENT_TYPE = "content_type"    # 内容类型：网址 或 纯文本
FIELD_CONTENT      = "content"         # 实际内容：完整的 URL 或文本
FIELD_TITLE        = "title"           # 标题：网页标题，或文本的简短描述
FIELD_CAPTURED_AT  = "captured_at"     # 捕获时间：什么时候复制/访问的
FIELD_ORIGIN       = "origin"          # 来源：剪贴板复制 还是 浏览器历史
FIELD_IS_PROCESSED = "is_processed"    # 是否已处理：0=未处理, 1=已处理
FIELD_CREATED_AT   = "created_at"      # 记录创建时间（由数据库自动填充）
FIELD_UPDATED_AT   = "updated_at"      # 记录最后更新时间

# 所有字段的列表，遍历建表/查询时用
ALL_FIELDS = [
    FIELD_ID,
    FIELD_CONTENT_TYPE,
    FIELD_CONTENT,
    FIELD_TITLE,
    FIELD_CAPTURED_AT,
    FIELD_ORIGIN,
    FIELD_IS_PROCESSED,
    FIELD_CREATED_AT,
    FIELD_UPDATED_AT,
]

# 内容类型只有两种：网址链接 或 纯文本
CONTENT_TYPE_URL  = "url"
CONTENT_TYPE_TEXT = "text"
VALID_CONTENT_TYPES = (CONTENT_TYPE_URL, CONTENT_TYPE_TEXT)

# 数据来源只有两种：从剪贴板复制的 或 从浏览器历史里来的
ORIGIN_CLIPBOARD       = "clipboard"
ORIGIN_BROWSER_HISTORY = "browser_history"
VALID_ORIGINS = (ORIGIN_CLIPBOARD, ORIGIN_BROWSER_HISTORY)

# 默认值：不填来源时默认为剪贴板，不填处理状态时默认为未处理
DEFAULT_ORIGIN       = "clipboard"
DEFAULT_IS_PROCESSED = 0

# ============================================================
# SQL 模板 — 建表和插入都用它们
# Python 会在运行时把 {} 里的变量替换成对应的名称，非常灵活
# ============================================================

# 建表 SQL — 表不存在才创建，重复运行不会丢数据
CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    {FIELD_ID}            INTEGER PRIMARY KEY AUTOINCREMENT,
    {FIELD_CONTENT_TYPE}  TEXT    NOT NULL CHECK({FIELD_CONTENT_TYPE} IN ('url', 'text')),
    {FIELD_CONTENT}       TEXT    NOT NULL,
    {FIELD_TITLE}         TEXT,
    {FIELD_CAPTURED_AT}   TEXT    NOT NULL,
    {FIELD_ORIGIN}        TEXT    NOT NULL DEFAULT '{DEFAULT_ORIGIN}',
    {FIELD_IS_PROCESSED}  INTEGER NOT NULL DEFAULT {DEFAULT_IS_PROCESSED},
    {FIELD_CREATED_AT}    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    {FIELD_UPDATED_AT}    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

# 插入 SQL — 用 ? 占位符防止 SQL 注入
INSERT_SQL = f"""
INSERT INTO {TABLE_NAME}
    ({FIELD_CONTENT_TYPE}, {FIELD_CONTENT}, {FIELD_TITLE}, {FIELD_CAPTURED_AT}, {FIELD_ORIGIN})
VALUES (?, ?, ?, ?, ?)
"""

# 索引列表 — 给常用查询字段建索引，数据多了也能快速搜索
INDEX_SQLS = [
    f"CREATE INDEX IF NOT EXISTS idx_type ON {TABLE_NAME}({FIELD_CONTENT_TYPE})",
    f"CREATE INDEX IF NOT EXISTS idx_processed ON {TABLE_NAME}({FIELD_IS_PROCESSED})",
    f"CREATE INDEX IF NOT EXISTS idx_captured_at ON {TABLE_NAME}({FIELD_CAPTURED_AT})",
    f"CREATE INDEX IF NOT EXISTS idx_type_proc ON {TABLE_NAME}({FIELD_CONTENT_TYPE}, {FIELD_IS_PROCESSED})",
]


# ============================================================
# 用户摘要数据库 — 面向用户的 AI 友好摘要
# 原始数据存入 clipboard_captures 后，AI 自动生成摘要存入此表
# ============================================================

USER_TABLE_NAME = "user_summaries"

UFIELD_ID                  = "id"
UFIELD_SOURCE_ID           = "source_id"            # FK → clipboard_captures.id
UFIELD_TITLE               = "title"                # AI 生成的友好标题
UFIELD_SUMMARY             = "summary"              # 一句话概述
UFIELD_TAGS                = "tags"                 # 逗号分隔的分类标签
UFIELD_DEEP_SUMMARY        = "deep_summary"         # 完整 AISummarizer 输出（按需生成）
UFIELD_IS_DEEP_SUMMARIZED  = "is_deep_summarized"   # 0=未深度总结, 1=已完成
UFIELD_CREATED_AT          = "created_at"
UFIELD_UPDATED_AT          = "updated_at"

ALL_USER_FIELDS = [
    UFIELD_ID,
    UFIELD_SOURCE_ID,
    UFIELD_TITLE,
    UFIELD_SUMMARY,
    UFIELD_TAGS,
    UFIELD_DEEP_SUMMARY,
    UFIELD_IS_DEEP_SUMMARIZED,
    UFIELD_CREATED_AT,
    UFIELD_UPDATED_AT,
]

CREATE_USER_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {USER_TABLE_NAME} (
    {UFIELD_ID}                  INTEGER PRIMARY KEY AUTOINCREMENT,
    {UFIELD_SOURCE_ID}           INTEGER NOT NULL,
    {UFIELD_TITLE}               TEXT,
    {UFIELD_SUMMARY}             TEXT,
    {UFIELD_TAGS}                TEXT,
    {UFIELD_DEEP_SUMMARY}        TEXT,
    {UFIELD_IS_DEEP_SUMMARIZED}  INTEGER NOT NULL DEFAULT 0,
    {UFIELD_CREATED_AT}          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    {UFIELD_UPDATED_AT}          TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY ({UFIELD_SOURCE_ID}) REFERENCES {TABLE_NAME}({FIELD_ID}) ON DELETE CASCADE
)
"""

INSERT_USER_SQL = f"""
INSERT INTO {USER_TABLE_NAME}
    ({UFIELD_SOURCE_ID}, {UFIELD_TITLE}, {UFIELD_SUMMARY}, {UFIELD_TAGS})
VALUES (?, ?, ?, ?)
"""

UPDATE_DEEP_SUMMARY_SQL = f"""
UPDATE {USER_TABLE_NAME}
SET {UFIELD_DEEP_SUMMARY} = ?,
    {UFIELD_IS_DEEP_SUMMARIZED} = 1,
    {UFIELD_UPDATED_AT} = datetime('now', 'localtime')
WHERE {UFIELD_ID} = ?
"""

USER_INDEX_SQLS = [
    f"CREATE INDEX IF NOT EXISTS idx_user_source ON {USER_TABLE_NAME}({UFIELD_SOURCE_ID})",
    f"CREATE INDEX IF NOT EXISTS idx_user_deep ON {USER_TABLE_NAME}({UFIELD_IS_DEEP_SUMMARIZED})",
    f"CREATE INDEX IF NOT EXISTS idx_user_created ON {USER_TABLE_NAME}({UFIELD_CREATED_AT})",
    f"CREATE INDEX IF NOT EXISTS idx_user_tags ON {USER_TABLE_NAME}({UFIELD_TAGS})",
]


# ============================================================
# 第二部分：ClipboardDB — 数据库管家
#
# 职责：
#   1. 连接数据库，确保表和索引都存在
#   2. 提供 receive() 方法，接收一条数据并存入
#   3. 用完记得 close() 关闭连接
#
# 使用示例：
#   db = ClipboardDB()
#   record_id = db.receive({
#       "type": "url",
#       "content": "https://example.com",
#       "title": "示例网站",
#       "timestamp": "2026-07-29 15:00:00",
#       "origin": "clipboard",
#   })
#   db.close()
# ============================================================

class ClipboardDB:
    """剪贴板数据库 — 只管存，不管查（查数据用后面的 QueryEngine）"""

    def __init__(self, db_path: str = DB_PATH,
                 user_db: "UserSummaryDB | None" = None,
                 light_summarizer: "LightweightSummarizer | None" = None):
        # 连接数据库，row_factory = Row 让查询结果可以用字段名访问
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL 模式：允许多个读取者同时操作，不会互相阻塞
        self.conn.execute("PRAGMA journal_mode=WAL")
        # 确保表和索引都已建好，放心写数据
        self._ensure_table()
        # 可选：自动摘要依赖
        self.user_db = user_db
        self.light_summarizer = light_summarizer

    def _ensure_table(self):
        """建表 + 建索引（IF NOT EXISTS 保证重复调用也安全）"""
        self.conn.execute(CREATE_TABLE_SQL)
        for sql in INDEX_SQLS:
            self.conn.execute(sql)
        self.conn.commit()

    def receive(self, data: dict) -> (int, int | None):
        """
        存一条数据到数据库。如果已有相同内容（type + content + title + origin）
        的记录，只更新时间戳，不重复插入，不重新生成摘要。

        接收一个字典，必须包含 type 和 content 字段，title、timestamp、origin 可选。
        返回 (source_id, summary_id) — summary_id 在未配置自动摘要时为 None。

        示例输入：
            {
                "type": "url",
                "content": "https://docs.python.org/3/library/sqlite3.html",
                "title": "Python sqlite3 官方文档",
                "timestamp": "2026-07-29 15:00:00",
                "origin": "clipboard"
            }
        """
        # 从字典里取出各字段，缺的用默认值
        content_type = data["type"]
        content      = data["content"]
        title        = data.get("title", "")
        captured_at  = data.get("timestamp",
                               datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        origin       = data.get("origin", DEFAULT_ORIGIN)

        # 类型不对就报错 — 防止把奇怪的东西写进数据库
        if content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"类型错误！只能是 {VALID_CONTENT_TYPES}，收到了: {content_type!r}")

        # 来源不对也报错
        if origin not in VALID_ORIGINS:
            raise ValueError(f"来源错误！只能是 {VALID_ORIGINS}，收到了: {origin!r}")

        # ── 去重：查是否已有同内容记录（忽略时间） ──
        existing = self.conn.execute(
            f"""SELECT {FIELD_ID} FROM {TABLE_NAME}
                WHERE {FIELD_CONTENT_TYPE} = ?
                  AND {FIELD_CONTENT} = ?
                  AND {FIELD_TITLE} = ?
                  AND {FIELD_ORIGIN} = ?
                LIMIT 1""",
            (content_type, content, title, origin),
        ).fetchone()

        if existing:
            # 已有旧记录 → 删掉旧的（含关联摘要），下面走正常插入+AI摘要
            old_id = existing[FIELD_ID]
            if self.user_db:
                old_summary = self.user_db.get_by_source_id(old_id)
                if old_summary:
                    self.user_db.delete(old_summary[UFIELD_ID])
            self.conn.execute(
                f"DELETE FROM {TABLE_NAME} WHERE {FIELD_ID} = ?",
                (old_id,),
            )
            self.conn.commit()
            print(f"  [去重] 删除旧记录 source_id={old_id}，即将写入新记录")

        # 执行插入，返回新记录的 id
        cur = self.conn.execute(
            INSERT_SQL,
            (content_type, content, title, captured_at, origin),
        )
        self.conn.commit()
        source_id = cur.lastrowid

        # ── 自动摘要：新记录才生成摘要 ──
        summary_id = None
        if self.user_db and self.light_summarizer:
            try:
                summary_data = self.light_summarizer.summarize(data)
                if summary_data:
                    summary_id = self.user_db.insert_summary(
                        source_id,
                        summary_data["title"],
                        summary_data["summary"],
                        summary_data["tags"],
                    )
                    print(f"  [自动摘要] source_id={source_id} → summary_id={summary_id}")
            except Exception as e:
                print(f"  [警告] 自动摘要失败 (source_id={source_id}): {e}")
                # 摘要失败不影响原始数据写入

        return source_id, summary_id

    def close(self):
        """关闭数据库连接，养成用完就关的好习惯"""
        self.conn.close()


# ============================================================
# 第三部分：QueryEngine — 搜索引擎
#
# 职责：
#   把"想查什么"翻译成 SQL，去数据库里找结果。
#   支持关键词、类型、来源、时间范围、处理状态等任意组合查询。
#
# 使用示例：
#   engine = QueryEngine(db)
#   results = engine.search(
#       keywords=["Python"],          # 搜标题和内容里包含 "Python" 的
#       content_type="url",           # 只看网址类型的
#       date_from="2026-07-01",       # 7月1日之后的
#       limit=10                      # 最多返回10条
#   )
# ============================================================

class QueryEngine:
    """查询引擎 — 你想怎么搜，它就来拼 SQL"""

    def __init__(self, db: "ClipboardDB"):
        self.db = db

    def search(
        self,
        keywords: list | str | None = None,      # 关键词，可以是字符串或字符串列表
        content_type: str | None = None,          # 只看 "url" 或 "text"
        origin: str | None = None,                # 只看 "clipboard" 或 "browser_history"
        date_from: str | None = None,             # 起始时间 "2026-07-01" 或带时分秒
        date_to: str | None = None,               # 截止时间
        is_processed: int | None = None,          # 只看已处理(1)或未处理(0)
        limit: int = 20,                          # 最多返回多少条
        offset: int = 0,                          # 跳过前面多少条（翻页用）
        order_by: str = "captured_at DESC",       # 排序方式，默认按捕获时间倒序
    ) -> list[dict]:
        """
        根据你给的条件去数据库里搜，返回匹配的字典列表。

        所有条件都是可选的，不传的就不管。比如只传 content_type="url"
        就会返回所有网址类型的记录。
        """
        # 先拼 WHERE 子句和参数
        where_clause, params = self._build_where(
            keywords=keywords,
            content_type=content_type,
            origin=origin,
            date_from=date_from,
            date_to=date_to,
            is_processed=is_processed,
        )

        # 拼完整 SQL — SELECT + WHERE + ORDER BY + LIMIT/OFFSET
        sql = f"""
            SELECT *
            FROM {TABLE_NAME}
            {where_clause}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        rows = self.db.conn.execute(sql, params).fetchall()
        # 把 sqlite3.Row 对象转成普通的字典，方便调用方使用
        return [dict(row) for row in rows]

    def get_by_id(self, record_id: int) -> dict | None:
        """按 id 查一条记录，找不到就返回 None"""
        row = self.db.conn.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE {FIELD_ID} = ?",
            (record_id,),
        ).fetchone()
        return dict(row) if row else None

    def count(
        self,
        keywords: list | str | None = None,
        content_type: str | None = None,
        origin: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        is_processed: int | None = None,
    ) -> int:
        """
        返回符合条件的总记录数，不做分页。
        比如你想在界面上显示"共找到 42 条结果"时就用它。
        """
        where_clause, params = self._build_where(
            keywords=keywords,
            content_type=content_type,
            origin=origin,
            date_from=date_from,
            date_to=date_to,
            is_processed=is_processed,
        )
        row = self.db.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM {TABLE_NAME} {where_clause}",
            params,
        ).fetchone()
        return row["cnt"]

    def _build_where(
        self,
        keywords: list | str | None = None,
        content_type: str | None = None,
        origin: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        is_processed: int | None = None,
    ) -> tuple[str, list]:
        """
        WHERE 子句拼装器（内部方法，不对外暴露）。

        根据传进来的参数，动态拼接 WHERE 条件。
        每个条件用 AND 连接，同时收集对应的 ? 占位参数，防止 SQL 注入。

        返回：(WHERE 子句字符串, 参数列表)
        如果没有任何条件，就返回 ("", [])，此时查询返回所有记录。
        """
        conditions = []   # 存一个个 "字段 = ?" 的字符串
        params = []       # 存对应的参数值

        # 关键词搜索 — 同时搜标题和内容两列，多个关键词之间是 AND 关系
        if keywords:
            # 如果只传了一个字符串，包成列表方便统一处理
            if isinstance(keywords, str):
                keywords = [keywords]
            for kw in keywords:
                # 用 % 包起来实现模糊匹配，前后都能命中
                like_pattern = f"%{kw}%"
                conditions.append(
                    f"({FIELD_TITLE} LIKE ? OR {FIELD_CONTENT} LIKE ?)"
                )
                params.extend([like_pattern, like_pattern])

        # 精确匹配 — 类型、来源、处理状态
        if content_type is not None:
            conditions.append(f"{FIELD_CONTENT_TYPE} = ?")
            params.append(content_type)

        if origin is not None:
            conditions.append(f"{FIELD_ORIGIN} = ?")
            params.append(origin)

        if is_processed is not None:
            conditions.append(f"{FIELD_IS_PROCESSED} = ?")
            params.append(is_processed)

        # 时间范围 — 支持只设起点、只设终点，或两者都设
        if date_from is not None:
            conditions.append(f"{FIELD_CAPTURED_AT} >= ?")
            params.append(date_from)

        if date_to is not None:
            conditions.append(f"{FIELD_CAPTURED_AT} <= ?")
            params.append(date_to)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        return where_clause, params


# ============================================================
# DeepSeek API 核心调用函数
# 所有需要跟 AI 对话的地方都通过这个函数，统一管理
# ============================================================

def _call_deepseek(system_prompt: str, user_message: str, api_key: str | None = None, max_tokens: int = 2000) -> str | None:
    """
    调用 DeepSeek API，返回 AI 的文本回复。

    如果 API Key 还是默认值（没填），直接返回 None，表示 AI 未接入。
    调用失败时也返回 None，上游代码会自动降级。

    参数:
        system_prompt: 告诉 AI 它是什么角色
        user_message:  用户的问题或要给 AI 处理的内容
        api_key:       不传就用全局 DEEPSEEK_API_KEY
        max_tokens:    最大输出 token 数，默认 2000

    返回:
        AI 的文本回复，失败时返回 None
    """
    if api_key is None:
        api_key = DEEPSEEK_API_KEY

    # 检查 API Key 是否被用户填写过
    if "你的API-Key填在这里" in api_key or not api_key.strip():
        print("  [提示] DeepSeek API Key 未设置，跳过 AI 调用")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(DEEPSEEK_BASE_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        print(f"  [DeepSeek API 调用失败] {e}")
        return None


# ============================================================
# 第四部分：AIQueryProcessor — AI 大脑（预留接口）
#
# 未来接入 AI 后，用户就可以用大白话提问，比如：
#   "上周我都复制了哪些 Python 的网址？"
#   "浏览器历史里有没有 GitHub 的链接？"
#
# AI 会把这些话翻译成 QueryEngine 能用的结构化参数，
# 然后交给搜索引擎去查，最后把结果返回给用户。
#
# 目前 AI 还没接，这里只搭好了框架和提示词模板。
# 接入方法：只需修改 process_query() 方法，调用 AI API 即可。
# ============================================================


# ============================================================
# 第三点五部分：LightweightSummarizer — 轻量级 AI 摘要
#
# 职责：
#   ClipboardDB 每存入一条新记录后，自动生成简短的标题+摘要+标签。
#   不访问网页，只根据 URL 或文本内容做一次 API 调用，速度快（≤3秒）。
#
# 与 AISummarizer 的区别：
#   - LightweightSummarizer: 存数据时自动调用，轻快，不抓网页
#   - AISummarizer:           用户手动触发，完整深度总结（抓网页+AI）
# ============================================================

class LightweightSummarizer:
    """
    轻量级摘要器 — 在数据存入时自动生成用户友好的摘要。

    输入: clipboard_captures 的一条记录 {content_type, content, title}
    输出: {title, summary, tags} 或 None（AI 不可用时）

    设计原则:
      - 一次 API 调用，max_tokens=300 控制成本
      - 不访问任何网页
      - 失败返回 None 不阻塞写入
    """

    SYSTEM_PROMPT = """你是一个内容摘要助手。用户会给你一段内容（可能是 URL 或文本），请你生成一个简短友好的 JSON 摘要。

输出格式（严格的 JSON，不要加任何其他内容）：
{
    "title": "用户友好的简短标题（中文，不超过50字）",
    "summary": "一句话概述这条内容是什么（中文，不超过120字）",
    "tags": ["标签1", "标签2", "标签3"]
}

规则：
- 如果是 URL：根据 URL 路径和文件名推断内容主题，不要编造具体内容
- 如果是文本：直接概括文本的核心意思
- 标签用英文小写，3-5 个，如 "python", "database", "tutorial"
- 标题要做到让人一看就知道这条记录大概是什么"""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key if api_key is not None else DEEPSEEK_API_KEY

    def summarize(self, source_record: dict) -> dict | None:
        """
        对一条原始记录生成轻量级摘要。

        参数:
            source_record: 来自 clipboard_captures 的记录字典

        返回:
            {"title": "...", "summary": "...", "tags": "tag1,tag2"} 或 None
        """
        ct = source_record.get("content_type", "")
        content = source_record.get("content", "")
        title = source_record.get("title", "")

        if ct == CONTENT_TYPE_URL:
            user_msg = f"URL: {content}\n原标题: {title}\n请根据 URL 推断内容主题并生成摘要。"
        else:
            preview = content[:500] if len(content) > 500 else content
            user_msg = f"这是一段文本内容，请生成摘要：\n\n{preview}"

        response = _call_deepseek(self.SYSTEM_PROMPT, user_msg, self.api_key, max_tokens=300)
        if response is None:
            return None

        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("\n", 1)[0] if "\n" in cleaned else cleaned[:-3]
            result = json.loads(cleaned)
            tags = result.get("tags", [])
            if isinstance(tags, list):
                tags = ",".join(tags[:5])
            return {
                "title": result.get("title", title or "未命名"),
                "summary": result.get("summary", ""),
                "tags": tags,
            }
        except (json.JSONDecodeError, KeyError):
            print(f"  [警告] LightweightSummarizer JSON 解析失败: {response[:100]}")
            return None


# ============================================================
# 第三点六部分：UserSummaryDB — 用户摘要数据库
#
# 职责：
#   1. 管理 user_summaries 表（建表、索引）
#   2. 插入 AI 生成的摘要
#   3. 更新深度摘要（用户手动触发时）
#   4. 提供查询接口
# ============================================================

class UserSummaryDB:
    """用户摘要数据库 — 面向用户的可读内容库"""

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_table()

    def _ensure_table(self):
        """建表 + 建索引"""
        self.conn.execute(CREATE_USER_TABLE_SQL)
        for sql in USER_INDEX_SQLS:
            self.conn.execute(sql)
        self.conn.commit()

    def insert_summary(self, source_id: int, title: str, summary: str, tags: str) -> int:
        """插入一条轻量摘要，返回新记录的 id"""
        cur = self.conn.execute(
            INSERT_USER_SQL,
            (source_id, title, summary, tags),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_deep_summary(self, summary_id: int, deep_summary: str):
        """更新某条记录的深度摘要"""
        self.conn.execute(UPDATE_DEEP_SUMMARY_SQL, (deep_summary, summary_id))
        self.conn.commit()

    def delete(self, summary_id: int):
        """删除一条摘要"""
        self.conn.execute(
            f"DELETE FROM {USER_TABLE_NAME} WHERE {UFIELD_ID} = ?",
            (summary_id,),
        )
        self.conn.commit()

    def get_by_id(self, summary_id: int) -> dict | None:
        """按 id 查一条摘要"""
        row = self.conn.execute(
            f"SELECT * FROM {USER_TABLE_NAME} WHERE {UFIELD_ID} = ?",
            (summary_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_by_source_id(self, source_id: int) -> dict | None:
        """按原始记录 id 查摘要"""
        row = self.conn.execute(
            f"SELECT * FROM {USER_TABLE_NAME} WHERE {UFIELD_SOURCE_ID} = ?",
            (source_id,),
        ).fetchone()
        return dict(row) if row else None

    def search(
        self,
        keywords: str | None = None,
        tags: str | None = None,
        is_deep_summarized: int | None = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = f"{UFIELD_CREATED_AT} DESC",
    ) -> list[dict]:
        """
        在 user_summaries 中搜索。

        参数:
            keywords: 在 title、summary、tags 中模糊匹配
            tags: 标签过滤（LIKE 匹配）
            is_deep_summarized: 0=未深度总结, 1=已完成, None=全部
        """
        conditions = []
        params = []

        if keywords:
            for kw in keywords.split():
                like = f"%{kw}%"
                conditions.append(
                    f"({UFIELD_TITLE} LIKE ? OR {UFIELD_SUMMARY} LIKE ? OR {UFIELD_TAGS} LIKE ?)"
                )
                params.extend([like, like, like])

        if tags:
            conditions.append(f"{UFIELD_TAGS} LIKE ?")
            params.append(f"%{tags}%")

        if is_deep_summarized is not None:
            conditions.append(f"{UFIELD_IS_DEEP_SUMMARIZED} = ?")
            params.append(is_deep_summarized)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM {USER_TABLE_NAME} {where} ORDER BY {order_by} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def count(
        self,
        keywords: str | None = None,
        tags: str | None = None,
        is_deep_summarized: int | None = None,
    ) -> int:
        """返回匹配的记录总数"""
        conditions = []
        params = []

        if keywords:
            for kw in keywords.split():
                like = f"%{kw}%"
                conditions.append(
                    f"({UFIELD_TITLE} LIKE ? OR {UFIELD_SUMMARY} LIKE ? OR {UFIELD_TAGS} LIKE ?)"
                )
                params.extend([like, like, like])

        if tags:
            conditions.append(f"{UFIELD_TAGS} LIKE ?")
            params.append(f"%{tags}%")

        if is_deep_summarized is not None:
            conditions.append(f"{UFIELD_IS_DEEP_SUMMARIZED} = ?")
            params.append(is_deep_summarized)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) AS cnt FROM {USER_TABLE_NAME} {where}",
            params,
        ).fetchone()
        return row["cnt"]

    def list_all(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """获取所有摘要（分页）"""
        return self.search(limit=limit, offset=offset)

    def close(self):
        """关闭数据库连接"""
        self.conn.close()


# ============================================================

class AIQueryProcessor:
    """
    AI 查询处理器 — 把"人话"翻译成"查询条件"。

    内部调用 DeepSeek API，把用户的自然语言问题（比如"上周复制的 Python 链接"）
    转成 QueryEngine 能执行的结构化查询参数。

    如果 API Key 没填写，自动回退到空白查询（返回所有记录）。
    """

    # ==========================================================
    # 角色说明书 (System Prompt) — 告诉 AI 它是谁、该怎么做
    # ==========================================================
    SYSTEM_PROMPT = f"""你是一个查询参数提取助手。你的任务是把用户的自然语言问题，转换成结构化的查询参数 JSON。

## 你要查询的数据库长这样
- 表名：{TABLE_NAME}
- 每一列的含义：
  · id               — 记录编号（内部用，用户不会问）
  · content_type     — 内容类型："url"=网址, "text"=纯文本
  · content          — 完整内容（URL 地址或文本全文）
  · title            — 标题（网页标题或文本简述）
  · captured_at      — 捕获时间
  · origin           — 数据来源："clipboard"=剪贴板, "browser_history"=浏览器历史
  · is_processed     — 是否处理过：0=没处理, 1=已处理

## 你要输出的 JSON 格式（严格按照这个结构，不要多也不要少）
{{
    "keywords": ["关键词1", "关键词2"],   // 从用户问题里提取的搜索词，没有就是空数组 []
    "content_type": "url",              // 用户说了要看网址就填 "url"，说看文本就填 "text"，没提就不填(null)
    "origin": "clipboard",              // 提到剪贴板就填 "clipboard"，提到浏览器历史就填 "browser_history"，没提就不填(null)
    "date_from": "2026-07-22",          // 时间范围的起点，格式 YYYY-MM-DD，没有就不填(null)
    "date_to": "2026-07-29",            // 时间范围的终点，格式同上
    "is_processed": null,               // 0=只看没处理的, 1=只看处理过的, null=不管
    "limit": 20                         // 最多返回几条，默认20
}}

## 时间词汇怎么换算成日期（以当前日期 2026-07-30 为参考）
- "今天"      → "2026-07-30"
- "昨天"      → "2026-07-29"
- "前天"      → "2026-07-28"
- "上周"      → date_from = "2026-07-23", date_to = "2026-07-30"
- "本周"      → date_from = 本周一, date_to = 今天
- "本月"      → date_from = "2026-07-01", date_to = "2026-07-30"
- "最近N天"   → date_from = N天前, date_to = 今天
- "7月20日"   → "2026-07-20"

## 重要规则（请严格遵守）
1. 你的回复里只能有 JSON 对象，不要加任何解释、问候、标点
2. 用户没提到的字段就设成 null 或空数组，不要瞎猜
3. 关键词要提取问题的核心主题，去掉"帮我""找一下""有没有"这类废话"""

    def __init__(self, api_key: str | None = None):
        """
        创建 AI 查询处理器实例。

        参数:
            api_key: DeepSeek API Key。不传就用全局 DEEPSEEK_API_KEY。
        """
        self.api_key = api_key if api_key is not None else DEEPSEEK_API_KEY

    def process_query(self, question: str) -> dict:
        """
        把用户的自然语言问题转成结构化查询参数。

        内部流程：
          1. 把 SYSTEM_PROMPT（角色说明书）和用户问题一起发给 DeepSeek
          2. DeepSeek 返回一个 JSON，包含 keywords、content_type 等查询条件
          3. 解析 JSON 并返回给上游的 ask() 函数

        API Key 没填或调用失败时返回空字典，
        上游会自动降级为土法关键词搜索。

        示例输入 → 输出：
          输入："上周复制的 Python 相关网址"
          输出：{{
              "keywords": ["Python"],
              "content_type": "url",
              "date_from": "2026-07-23",
              "date_to": "2026-07-30",
              "limit": 20
          }}
        """
        # 调用 DeepSeek，让 AI 解析用户的自然语言问题
        response = _call_deepseek(self.SYSTEM_PROMPT, question, self.api_key)

        # AI 调用失败 → 返回空字典，让上游启用土法降级
        if response is None:
            return {}

        # 解析 AI 返回的 JSON
        try:
            cleaned = response.strip()
            # 去掉可能的 markdown 代码块标记 ```json ... ```
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned.rsplit("\n", 1)[0] if "\n" in cleaned else cleaned[:-3]
            filters = json.loads(cleaned)
            # 去掉 AI 可能塞进来的无关字段
            allowed_keys = {"keywords", "content_type", "origin", "date_from", "date_to", "is_processed", "limit"}
            return {k: v for k, v in filters.items() if k in allowed_keys}
        except json.JSONDecodeError:
            # AI 返回了不合规的内容，降级
            print(f"  [警告] AI 返回无法解析，原文: {response[:100]}")
            return {}



# ============================================================
# 第四点五部分：AISummarizer — AI 智能摘要
#
# 职责：
#   拿到查询结果后，对不同类型的数据做智能处理：
#   - 如果内容是网址（url）：AI 会先去抓取网页内容，然后给你总结
#   - 如果内容是文本（text）：AI 直接帮你总结要点
#
# 使用示例：
#   summarizer = AISummarizer()
#   summary = summarizer.summarize(record)
#   print(summary["ai_summary"])   // "这个网页讲述了 Python sqlite3 模块的..."
# ============================================================

class AISummarizer:
    """
    AI 摘要器 — 对搜索结果进行智能总结。

    它有两种工作模式：
      1. 网址模式：访问网页 → 提取正文 → AI 总结
      2. 文本模式：直接把文本发给 AI → AI 总结
    """

    # ── 网址摘要用的 System Prompt ─────────────────────────
    SUMMARIZE_URL_PROMPT = """你是一个智能内容摘要助手。用户会给你URL、页面元数据和提取到的页面文本，请你智能判断内容类型并总结。

### 如果元数据显示是视频页面（og:type=video 或包含 video 相关标记）：
1. 用一句话概括这个视频的主题
2. 基于元数据中的标题、简介、标签等信息，梳理视频核心内容（3-8 个要点）
3. 如果页面文本中包含字幕、评论区讨论等有效信息，一并提炼

### 如果是普通网页/文章：
1. 用一句话概括网页讲的是什么
2. 列出最核心的 3-5 个要点
3. 如果内容包含代码/技术细节，简要说明涉及的技术栈

注意：
- 用中文回复
- 控制在 500 字以内
- 如果内容不完整或无法判断，基于已有信息尽力而为并据实说明"""

    # ── 文本摘要用的 System Prompt ─────────────────────────
    SUMMARIZE_TEXT_PROMPT = """你是一个文本摘要助手。用户会给你一段文本，请你：

1. 用一句话概括这段文本的主题
2. 提取文本中的关键信息，列成要点

注意：
- 用中文回复
- 控制在 200 字以内
- 如果文本很短，直接概括即可"""

    def __init__(self, api_key: str | None = None):
        """
        创建摘要器实例。

        参数:
            api_key: DeepSeek API Key。不传就用全局 DEEPSEEK_API_KEY。
        """
        self.api_key = api_key if api_key is not None else DEEPSEEK_API_KEY

    def summarize(self, record: dict) -> dict:
        """
        对一条数据库记录进行 AI 摘要。

        参数:
            record: 数据库查询返回的一条记录（字典）

        返回:
            在原字典基础上新增了 ai_summary 字段的新字典。
            如果 AI 未接入，ai_summary 会是一个提示文本。

        处理逻辑：
            content_type == "url"  → 先抓取网页，再总结
            content_type == "text" → 直接总结文本
        """
        result = dict(record)  # 不修改原始记录，复制一份

        if record["content_type"] == CONTENT_TYPE_URL:
            result["ai_summary"] = self._summarize_url(record["content"])
        else:
            result["ai_summary"] = self._summarize_text(record["content"])

        return result

    def summarize_batch(self, records: list[dict]) -> list[dict]:
        """
        对多条记录逐一摘要，返回带了 ai_summary 的列表。

        每条记录都会打印进度，方便了解处理状态。
        """
        results = []
        for i, record in enumerate(records, 1):
            print(f"  [AI 摘要] 处理第 {i}/{len(records)} 条: {record.get('title', record['content'][:40])}")
            results.append(self.summarize(record))
        return results

    def _summarize_url(self, url: str) -> str:
        """
        处理网址类型（文章/视频均适用）：
        1. 访问网址，抓取网页 HTML
        2. 从 HTML 中提取标准元数据（Open Graph、JSON-LD、meta 标签等）
        3. 清洗出页面纯文本
        4. 将元数据和文本一起发给 DeepSeek，让 AI 智能判断内容类型并总结

        任何支持 Open Graph / JSON-LD 的视频网站都能被正确识别，
        不限定特定平台。
        """
        print(f"    正在访问网页: {url[:80]}...")

        html = self._fetch_html(url)
        if html is None:
            return f"[无法访问该网页，以下为原始链接]\n{url}"

        # 从原始 HTML 提取元数据（在清洗之前做）
        meta = self._extract_metadata(html, url)

        # 清洗 HTML → 纯文本
        page_text = self._html_to_text(html)

        print(f"    已获取网页内容 ({len(page_text)} 字符)，元数据字段: {list(meta.keys())}，正在请 AI 总结...")

        # 构造丰富的 prompt，让 AI 根据元数据 + 文本智能判断
        meta_desc = meta.get("description", "")
        meta_title = meta.get("title", "")
        meta_type = meta.get("type", "")
        meta_keywords = meta.get("keywords", "")
        meta_structured = meta.get("structured", {})

        user_message = f"""URL: {url}

## 页面元数据
- 标题: {meta_title}
- 描述: {meta_desc}
- 类型: {meta_type}
- 关键词: {meta_keywords}
- 结构化数据: {json.dumps(meta_structured, ensure_ascii=False) if meta_structured else '无'}

## 页面提取文本
{page_text}"""

        summary = _call_deepseek(self.SUMMARIZE_URL_PROMPT, user_message, self.api_key)

        if summary is None:
            return f"[AI 未接入，网页内容已抓取但无法生成摘要]\nURL: {url}\n网页原文前 200 字:\n{page_text[:200]}"

        return summary.strip()

    def _summarize_text(self, text: str) -> str:
        """
        处理纯文本类型：直接把文本发给 DeepSeek 做总结。
        """
        print(f"    正在请 AI 总结文本 ({len(text)} 字符)...")

        user_message = f"请总结以下文本的内容：\n\n{text}"
        summary = _call_deepseek(self.SUMMARIZE_TEXT_PROMPT, user_message, self.api_key)

        if summary is None:
            return f"[AI 未接入，无法生成摘要]\n原文:\n{text[:200]}"

        return summary.strip()

    def _fetch_html(self, url: str) -> str | None:
        """
        下载网页的原始 HTML。

        返回:
            原始 HTML 字符串，失败返回 None
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            }
            resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as e:
            print(f"    [网页访问失败] {e}")
            return None

    @staticmethod
    def _html_to_text(html: str) -> str:
        """
        把 HTML 清洗为纯文本。

        步骤：
        1. 去掉 script / style / nav / footer 等非正文标签
        2. 去掉所有 HTML 标签，只留文字
        3. 处理 HTML 实体，合并多余空白
        4. 截取前 8000 字符，控制 AI 输入长度
        """
        # 去掉不会出现在正文里的标签
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # 去掉所有 HTML 标签，只保留文字
        text = re.sub(r'<[^>]+>', ' ', html)
        # 把 HTML 实体换成普通字符
        text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&amp;', '&').replace('&quot;', '"')
        # 合并连续空白
        text = re.sub(r'\s+', ' ', text).strip()

        if not text or len(text) < 10:
            return "(页面内容为空或太少)"

        return text[:8000]

    @staticmethod
    def _extract_metadata(html: str, url: str) -> dict:
        """
        从 HTML 中提取标准元数据（不限定任何平台）。

        提取策略（按优先级）：
        1. Open Graph 标签 — og:title, og:description, og:type, og:video:*
           → 几乎所有视频网站（B站、YouTube、Vimeo 等）和社交平台都使用
        2. JSON-LD 结构化数据 — schema.org 的 VideoObject / Article
           → YouTube、各大新闻网站的标准配置
        3. 标准 <meta> 标签 — description, keywords
        4. <title> 标签 — 兜底

        返回:
            包含 title, description, type, keywords, structured 的字典
        """
        meta: dict[str, str] = {}
        structured: dict[str, str] = {}

        # ── 1. Open Graph 标签 ─────────────────────────────
        # 匹配属性值中有双引号的情况
        for match in re.finditer(
            r'<meta\s[^>]*?property\s*=\s*["\']og:(title|description|type|video:tag)["\'][^>]*?'
            r'content\s*=\s*["\']([^"\']*?)["\'][^>]*?>',
            html, re.IGNORECASE
        ):
            key = match.group(1)
            value = match.group(2)
            if key == "title":
                meta.setdefault("title", value)
            elif key == "description":
                meta.setdefault("description", value)
            elif key == "type":
                meta["type"] = value
            elif key == "video:tag":
                existing = meta.get("keywords", "")
                meta["keywords"] = f"{existing}, {value}".strip(", ")

        # ── 2. JSON-LD 结构化数据 ──────────────────────────
        for match in re.finditer(
            r'<script[^>]*?type\s*=\s*["\']application/ld\+json["\'][^>]*?>(.*?)</script>',
            html, re.DOTALL | re.IGNORECASE
        ):
            try:
                ld_data = json.loads(match.group(1))
                # 可能是列表或单个对象
                if isinstance(ld_data, list):
                    ld_data = ld_data[0] if ld_data else {}
                ld_type = ld_data.get("@type", "")
                if ld_type in ("VideoObject", "Article", "WebPage", "NewsArticle",
                                "CreativeWork", "Movie", "TVEpisode"):
                    structured["@type"] = ld_type
                    structured["name"] = ld_data.get("name", "")
                    structured["description"] = ld_data.get("description", "")
                    if ld_type == "VideoObject":
                        structured["duration"] = ld_data.get("duration", "")
                        structured["uploadDate"] = ld_data.get("uploadDate", "")
                        # 尝试提取作者/频道名
                        author = ld_data.get("author", {})
                        if isinstance(author, dict):
                            structured["author"] = author.get("name", "")
                        elif isinstance(author, str):
                            structured["author"] = author
                    break  # 找到第一个有效的结构化数据就停
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # ── 3. 标准 meta 标签 ──────────────────────────────
        for match in re.finditer(
            r'<meta\s[^>]*?name\s*=\s*["\'](description|keywords)["\'][^>]*?'
            r'content\s*=\s*["\']([^"\']*?)["\'][^>]*?>',
            html, re.IGNORECASE
        ):
            key = match.group(1)
            value = match.group(2)
            meta.setdefault(key, value)

        # ── 4. <title> 标签 ────────────────────────────────
        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE)
        if title_match:
            page_title = title_match.group(1).strip()
            # 去掉常见的站点名后缀
            page_title = re.sub(r'\s*[-–|—]\s*.+$', '', page_title).strip()
            meta.setdefault("title", page_title)

        if structured:
            meta["structured"] = structured

        return meta


# ============================================================
# 第五部分：便捷函数 — 一行搞定搜索
# ============================================================

def ask(question: str, db: "ClipboardDB", processor: AIQueryProcessor | None = None) -> list[dict]:
    """
    一句话搜索入口：用大白话提问，直接拿到结果。

    它干了三件事：
      1. 用 AIQueryProcessor 把问题转成查询条件
      2. 如果 AI 没接入，自动降级为简单关键词提取
      3. 用 QueryEngine 去数据库搜索，返回结果

    用法：
      db = ClipboardDB()
      results = ask("最近关于 Python 的内容", db)
      for r in results:
          print(r["title"], r["content"])
    """
    if processor is None:
        processor = AIQueryProcessor()

    # 第一步：让 AI（或占位方法）解析问题
    filters = processor.process_query(question)

    # 第二步：AI 还没接入时，用土办法从问题里挑关键词
    if not filters:
        filters = _fallback_extract(question)

    # 第三步：搜！
    engine = QueryEngine(db)
    results = engine.search(**filters)

    return results



# ============================================================
# SummaryBrowserGUI — tkinter 图形界面
#
# 职责：
#   1. 主窗口用 Treeview 展示所有用户摘要记录
#   2. 点击某条记录 → 弹出详情窗口
#   3. 详情窗口可触发 AISummarizer 深度总结
#   4. 支持搜索和标签过滤
#
# 依赖：tkinter（Python 自带，无需安装）
# ============================================================

class SummaryBrowserGUI:
    """用户摘要浏览器 GUI — 基于 tkinter"""

    PAGE_SIZE = 50

    def __init__(self, user_db: "UserSummaryDB", source_db: "ClipboardDB",
                 deep_summarizer: "AISummarizer | None" = None):
        self.user_db = user_db
        self.source_db = source_db
        self.source_query = QueryEngine(source_db)
        self.deep_summarizer = deep_summarizer or AISummarizer()
        self._current_search = ""
        self._current_tag = ""
        self._filter_deep_only = False

    def launch(self):
        """启动 GUI 主循环"""
        import tkinter as tk
        from tkinter import ttk, messagebox

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox

        self.root = tk.Tk()
        self.root.title("用户摘要浏览器 — Clipboard Summary Browser")
        self.root.geometry("900x600")

        self._build_main_window()
        self.root.mainloop()

    # ── 主窗口构建 ─────────────────────────────────────────

    def _build_main_window(self):
        """构建主窗口布局"""
        tk = self.tk
        ttk = self.ttk

        # ── 顶部工具栏 ──────────────────────────────────────
        toolbar = ttk.Frame(self.root, padding="5")
        toolbar.pack(fill=tk.X)

        ttk.Label(toolbar, text="🔍 搜索:").pack(side=tk.LEFT, padx=(0, 5))
        self._search_var = tk.StringVar()
        search_entry = ttk.Entry(toolbar, textvariable=self._search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(0, 5))
        search_entry.bind("<Return>", lambda e: self._do_search())
        ttk.Button(toolbar, text="搜索", command=self._do_search).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(toolbar, text="🏷 标签过滤", command=self._filter_by_tag).pack(side=tk.LEFT, padx=(0, 5))

        self._deep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="仅显示未深度总结", variable=self._deep_var,
                        command=self._refresh_list).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(toolbar, text="🔄 重置", command=self._reset_filter).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(toolbar, text="📊 统计", command=self._show_stats).pack(side=tk.LEFT, padx=(0, 10))

        # ── Treeview 表格 ───────────────────────────────────
        tree_frame = ttk.Frame(self.root)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("title", "tags", "created_at", "deep")
        self._tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")

        self._tree.heading("title", text="标题", command=lambda: self._sort_by("title"))
        self._tree.heading("tags", text="标签")
        self._tree.heading("created_at", text="日期", command=lambda: self._sort_by("created_at"))
        self._tree.heading("deep", text="深度摘要")

        self._tree.column("title", width=380, minwidth=150)
        self._tree.column("tags", width=200, minwidth=80)
        self._tree.column("created_at", width=140, minwidth=80)
        self._tree.column("deep", width=80, minwidth=60, anchor=tk.CENTER)

        # 滚动条
        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 双击/回车查看详情
        self._tree.bind("<Double-1>", lambda e: self._open_detail())
        self._tree.bind("<Return>", lambda e: self._open_detail())

        # ── 底部状态栏 ──────────────────────────────────────
        status_frame = ttk.Frame(self.root, padding="5")
        status_frame.pack(fill=tk.X)

        self._status_label = ttk.Label(status_frame, text="就绪")
        self._status_label.pack(side=tk.LEFT)

        # 加载数据
        self._current_sort_col = "created_at"
        self._current_sort_desc = True
        self._refresh_list()

    # ── 数据刷新 ───────────────────────────────────────────

    def _refresh_list(self):
        """刷新 Treeview 中的记录列表"""
        tk = self.tk
        for item in self._tree.get_children():
            self._tree.delete(item)

        kwargs = {}
        if self._current_search:
            kwargs["keywords"] = self._current_search
        if self._current_tag:
            kwargs["tags"] = self._current_tag
        if self._deep_var.get():
            kwargs["is_deep_summarized"] = 0

        order_by = self._current_sort_col
        if self._current_sort_desc:
            order_by += " DESC"
        kwargs["order_by"] = order_by
        kwargs["limit"] = self.PAGE_SIZE

        records = self.user_db.search(**kwargs)
        total = self.user_db.count(
            keywords=kwargs.get("keywords"),
            tags=kwargs.get("tags"),
            is_deep_summarized=kwargs.get("is_deep_summarized"),
        )

        for r in records:
            deep_status = "✅" if r[UFIELD_IS_DEEP_SUMMARIZED] else "—"
            # 截断标题
            title = r[UFIELD_TITLE] or "(无标题)"
            if len(title) > 60:
                title = title[:57] + "..."
            tags = r[UFIELD_TAGS] or ""
            date = (r[UFIELD_CREATED_AT] or "")[:10]
            self._tree.insert("", tk.END, iid=r[UFIELD_ID],
                              values=(title, tags, date, deep_status))

        self._status_label.config(text=f"共 {total} 条记录 | 当前显示 {len(records)} 条 | 双击查看详情")

    def _sort_by(self, col: str):
        """切换排序列"""
        if self._current_sort_col == col:
            self._current_sort_desc = not self._current_sort_desc
        else:
            self._current_sort_col = col
            self._current_sort_desc = True
        self._refresh_list()

    # ── 操作按钮 ───────────────────────────────────────────

    def _do_search(self):
        self._current_search = self._search_var.get().strip()
        self._refresh_list()

    def _filter_by_tag(self):
        """弹出简单的标签输入对话框"""
        tk = self.tk
        dialog = tk.Toplevel(self.root)
        dialog.title("标签过滤")
        dialog.geometry("300x120")
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="输入标签关键词（如 python）:").pack(pady=(15, 5))
        entry = tk.Entry(dialog, width=30)
        entry.pack(pady=(0, 10))
        entry.insert(0, self._current_tag)
        entry.focus_set()

        def _apply():
            self._current_tag = entry.get().strip()
            self._refresh_list()
            dialog.destroy()

        entry.bind("<Return>", lambda e: _apply())
        tk.Button(dialog, text="确定", command=_apply).pack()

    def _reset_filter(self):
        self._current_search = ""
        self._current_tag = ""
        self._search_var.set("")
        self._deep_var.set(False)
        self._refresh_list()

    def _show_stats(self):
        """显示统计信息"""
        tk = self.tk
        total = self.user_db.count()
        deep_done = self.user_db.count(is_deep_summarized=1)
        deep_pending = self.user_db.count(is_deep_summarized=0)
        self.messagebox.showinfo(
            "统计",
            f"总记录数: {total}\n"
            f"已深度总结: {deep_done}\n"
            f"待深度总结: {deep_pending}"
        )

    # ── 详情弹窗 ───────────────────────────────────────────

    def _open_detail(self):
        """打开选中记录的详情窗口"""
        selection = self._tree.selection()
        if not selection:
            self.messagebox.showwarning("提示", "请先选择一条记录")
            return

        summary_id = int(selection[0])
        record = self.user_db.get_by_id(summary_id)
        if not record:
            return

        source = self.source_query.get_by_id(record[UFIELD_SOURCE_ID])
        self._show_detail_window(record, source)

    def _show_detail_window(self, record: dict, source: dict | None):
        """创建详情弹窗"""
        tk = self.tk
        ttk = self.ttk

        win = tk.Toplevel(self.root)
        win.title(f"📄 {record[UFIELD_TITLE] or '详情'}")
        win.geometry("700x550")
        win.transient(self.root)

        # ── 基本信息区 ──────────────────────────────────────
        info_frame = ttk.LabelFrame(win, text="基本信息", padding="10")
        info_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        fields = [
            ("标题", record[UFIELD_TITLE] or "(无)"),
            ("摘要", record[UFIELD_SUMMARY] or "(无)"),
            ("标签", record[UFIELD_TAGS] or "(无)"),
            ("创建日期", record[UFIELD_CREATED_AT] or ""),
        ]
        for i, (label, value) in enumerate(fields):
            ttk.Label(info_frame, text=f"{label}：", font=("", 9, "bold")).grid(
                row=i, column=0, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(info_frame, text=value, wraplength=550).grid(
                row=i, column=1, sticky=tk.W, pady=2)

        if source:
            src_type = f"{source[FIELD_CONTENT_TYPE]} | 来源: {source[FIELD_ORIGIN]}"
            ttk.Label(info_frame, text="原始类型：", font=("", 9, "bold")).grid(
                row=len(fields), column=0, sticky=tk.W, padx=(0, 10), pady=2)
            ttk.Label(info_frame, text=src_type).grid(
                row=len(fields), column=1, sticky=tk.W, pady=2)

        # ── 原始内容区 ──────────────────────────────────────
        content_frame = ttk.LabelFrame(win, text="原始内容", padding="10")
        content_frame.pack(fill=tk.X, padx=10, pady=5)

        src_content = source[FIELD_CONTENT] if source else "(无法获取)"
        content_text = tk.Text(content_frame, height=3, wrap=tk.WORD, font=("Consolas", 9))
        content_text.insert("1.0", src_content)
        content_text.config(state=tk.DISABLED)
        content_text.pack(fill=tk.X)

        # ── 深度摘要区 ──────────────────────────────────────
        deep_frame = ttk.LabelFrame(win, text="深度 AI 摘要", padding="10")
        deep_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        deep_text = tk.Text(deep_frame, height=8, wrap=tk.WORD, font=("", 10))
        deep_text.pack(fill=tk.BOTH, expand=True)

        if record[UFIELD_IS_DEEP_SUMMARIZED] and record[UFIELD_DEEP_SUMMARY]:
            deep_text.insert("1.0", record[UFIELD_DEEP_SUMMARY])
            deep_text.config(state=tk.DISABLED)
        else:
            deep_text.insert("1.0", "（尚未生成深度摘要 — 点击下方按钮生成）")
            deep_text.config(state=tk.DISABLED)

        # ── 按钮栏 ──────────────────────────────────────────
        btn_frame = ttk.Frame(win, padding="10")
        btn_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

        ttk.Button(btn_frame, text="关闭", command=win.destroy).pack(side=tk.RIGHT, padx=(5, 0))

        if not record[UFIELD_IS_DEEP_SUMMARIZED]:
            deep_btn = ttk.Button(
                btn_frame, text="🤖 AI 深度总结",
                command=lambda: self._run_deep_summary_thread(
                    record, source, deep_text, deep_btn, win)
            )
            deep_btn.pack(side=tk.RIGHT, padx=(5, 0))

    # ── 深度摘要（后台线程）─────────────────────────────────

    def _run_deep_summary_thread(self, record: dict, source: dict | None,
                                  text_widget, button, window):
        """在后台线程中执行深度摘要，避免 GUI 卡顿"""
        import threading

        if not source:
            self.messagebox.showwarning("提示", "无法找到原始记录")
            return

        button.config(text="⏳ 正在生成...", state="disabled")

        def _work():
            """后台线程：只做 AI 调用（不碰数据库，不碰 GUI）"""
            deep_result = self.deep_summarizer.summarize(source)
            deep_text = deep_result.get("ai_summary", str(deep_result))
            # 回到主线程：写库 + 更新 UI
            window.after(0, lambda: self._on_deep_done(
                record[UFIELD_ID], deep_text, text_widget, button))

        threading.Thread(target=_work, daemon=True).start()

    def _on_deep_done(self, summary_id: int, deep_text: str, text_widget, button):
        """主线程：数据库写入 + UI 更新（避免跨线程 SQLite 错误）"""
        # 存入数据库
        self.user_db.update_deep_summary(summary_id, deep_text)
        # 更新 UI
        text_widget.config(state="normal")
        text_widget.delete("1.0", "end")
        text_widget.insert("1.0", deep_text)
        text_widget.config(state="disabled")
        button.pack_forget()  # 隐藏按钮（已完成）


def _fallback_extract(question: str) -> dict:
    """
    土法关键词提取 — AI 没接时的降级方案。

    把用户问题中的语气词、疑问词去掉，剩下的字词直接拿去搜。
    准确度不高，但总比什么都搜不到强。
    等 AI 接入后，这个方法可以继续留着当备胎。
    """
    # 这些词在搜索里没什么意义，去掉它们
    meaningless_words = {
        "帮我", "查找", "搜索", "有没有", "找一下", "我想", "请问",
        "是什么", "怎么", "哪些", "什么", "哪个", "最近", "关于",
        "的", "了", "吗", "呢", "吧", "一下",
    }

    # 按标点符号把句子拆成词语
    tokens = re.split(r"[，,。\.\s、；;：:！!？?\"]+", question)
    useful_words = [
        t.strip() for t in tokens
        if t.strip() and t.strip() not in meaningless_words
    ]

    keywords = useful_words if useful_words else [question]
    return {"keywords": keywords, "limit": 20}


# ============================================================
# 测试代码 — 直接运行 python "create table.py" 就能跑
# 三个环节：写入数据 → 结构化查询 → 自然语言查询
# ============================================================

if __name__ == "__main__":
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("=" * 60)
    print("用户摘要浏览器 — 启动流程")
    print("=" * 60)

    # 第 1 步：创建所有组件
    print("\n[1] 初始化数据库和 AI 模块...")
    db = ClipboardDB()
    user_db = UserSummaryDB()
    light_sum = LightweightSummarizer()
    deep_sum = AISummarizer()

    # 把自动摘要组件注入 ClipboardDB
    db.user_db = user_db
    db.light_summarizer = light_sum

    # 第 2 步：写入测试数据（receive 会自动去重，相同内容只保留最新）
    print("\n[2] 写入测试数据（自动 AI 摘要）...")
    sample_data_list = [
            {
                "type": "url",
                "content": "https://docs.python.org/3/library/sqlite3.html",
                "title": "Python sqlite3 官方文档",
                "timestamp": "2026-07-28 10:30:00",
                "origin": "clipboard",
            },
            {
                "type": "text",
                "content": "深度学习（Deep Learning）是机器学习的一个分支，它使用多层神经网络来学习数据的层次化表示。",
                "title": "深度学习笔记",
                "timestamp": "2026-07-28 11:00:00",
                "origin": "clipboard",
            },
            {
                "type": "url",
                "content": "https://github.com/torvalds/linux",
                "title": "Linux 内核源码仓库",
                "timestamp": "2026-07-20 10:30:00",
                "origin": "browser_history",
            },
            {
                # ╔══════════════════════════════════════════════════════╗
                # ║  👇 把视频链接粘贴到下面这行的引号里，替换掉旧链接  ║
                # ╚══════════════════════════════════════════════════════╝
                "type": "url",
                "content": "https://www.bilibili.com/video/BV1iQVG6AEtS?spm_id_from=333.788.videopod.sections&vd_source=0ca753b1454488e30d399f469ee8c71a",  # ←── 在这里填入视频链接
                "title": "26年7月新番导视",
                "timestamp": "2026-07-31-18:49",
                "origin": "clipboard",
            },
        ]

    for data in sample_data_list:
        source_id, summary_id = db.receive(data)
        print(f"  [OK] source_id={source_id}, summary_id={summary_id} | {data['title'][:60]}")

    # 第 3 步：启动 GUI
    print("\n[3] 启动图形界面浏览器...")
    print("  " + "=" * 50)
    print("  GUI 窗口中：")
    print("  - 双击某条记录 → 查看详情")
    print("  - 在详情窗口点击「AI 深度总结」→ 生成完整摘要")
    print("  - 顶部搜索栏可关键词搜索")
    print("  " + "=" * 50)

    app = SummaryBrowserGUI(user_db, db, deep_sum)
    app.launch()

    # 清理
    user_db.close()
    db.close()
    print("\n程序结束。")
