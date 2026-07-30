
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
# DeepSeek API 配置 — 在这里填入你的 API Key
# 获取地址: https://platform.deepseek.com/api_keys
# ============================================================

DEEPSEEK_API_KEY = "sk-your-api-key-here"     # ← 把你的 API Key 填在这里
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

    def __init__(self, db_path: str = DB_PATH):
        # 连接数据库，row_factory = Row 让查询结果可以用字段名访问
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL 模式：允许多个读取者同时操作，不会互相阻塞
        self.conn.execute("PRAGMA journal_mode=WAL")
        # 确保表和索引都已建好，放心写数据
        self._ensure_table()

    def _ensure_table(self):
        """建表 + 建索引（IF NOT EXISTS 保证重复调用也安全）"""
        self.conn.execute(CREATE_TABLE_SQL)
        for sql in INDEX_SQLS:
            self.conn.execute(sql)
        self.conn.commit()

    def receive(self, data: dict) -> int:
        """
        存一条数据到数据库。

        接收一个字典，必须包含 type 和 content 字段，title、timestamp、origin 可选。
        返回新记录的自增 id。

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
        title        = data.get("title", "")                    # 标题可以空
        captured_at  = data.get("timestamp",                    # 没给时间戳就用当前时间
                               datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        origin       = data.get("origin", DEFAULT_ORIGIN)       # 没给来源就默认剪贴板

        # 类型不对就报错 — 防止把奇怪的东西写进数据库
        if content_type not in VALID_CONTENT_TYPES:
            raise ValueError(f"类型错误！只能是 {VALID_CONTENT_TYPES}，收到了: {content_type!r}")

        # 来源不对也报错
        if origin not in VALID_ORIGINS:
            raise ValueError(f"来源错误！只能是 {VALID_ORIGINS}，收到了: {origin!r}")

        # 执行插入，返回新记录的 id
        cur = self.conn.execute(
            INSERT_SQL,
            (content_type, content, title, captured_at, origin),
        )
        self.conn.commit()
        return cur.lastrowid

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

def _call_deepseek(system_prompt: str, user_message: str, api_key: str | None = None) -> str | None:
    """
    调用 DeepSeek API，返回 AI 的文本回复。

    如果 API Key 还是默认值（没填），直接返回 None，表示 AI 未接入。
    调用失败时也返回 None，上游代码会自动降级。

    参数:
        system_prompt: 告诉 AI 它是什么角色
        user_message:  用户的问题或要给 AI 处理的内容
        api_key:       不传就用全局 DEEPSEEK_API_KEY

    返回:
        AI 的文本回复，失败时返回 None
    """
    if api_key is None:
        api_key = DEEPSEEK_API_KEY

    # 检查 API Key 是否被用户填写过
    if api_key.startswith("sk-your-api-key") or not api_key.strip():
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
        "max_tokens": 2000,
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
    SUMMARIZE_URL_PROMPT = """你是一个网页内容摘要助手。用户会给你一个网页的文本内容，请你：

1. 先用一句话概括这个网页讲的是什么
2. 列出网页中最核心的 3-5 个要点
3. 如果内容包含代码/技术细节，简要说明涉及的技术栈

注意：
- 用中文回复
- 控制在 300 字以内
- 如果网页内容不完整或无法判断，据实说明"""

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
        处理网址类型：
        1. 访问网址，抓取网页内容
        2. 把提取到的文本发给 DeepSeek 做总结

        如果网页无法访问（超时、404 等），直接返回错误说明。
        """
        print(f"    正在访问网页: {url[:80]}...")

        page_text = self._fetch_page(url)
        if page_text is None:
            return f"[无法访问该网页，以下为原始链接]\n{url}"

        print(f"    已获取网页内容 ({len(page_text)} 字符)，正在请 AI 总结...")

        user_message = f"请总结以下网页的内容：\n\nURL: {url}\n\n网页内容:\n{page_text}"
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

    def _fetch_page(self, url: str) -> str | None:
        """
        抓取网页的文本内容。

        步骤：
        1. 用 requests 下载网页 HTML
        2. 去掉 script 和 style 标签（这些不是正文）
        3. 去掉所有 HTML 标签，只留文本
        4. 合并多余空白，截取前 8000 字发给 AI

        返回:
            提取后的纯文本，失败返回 None
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            }
            resp = requests.get(url, timeout=15, headers=headers)
            resp.raise_for_status()

            html = resp.text

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

            if not text or len(text) < 50:
                return None  # 内容太少，可能是需要 JS 渲染的页面

            # 截取前 8000 字符，控制 AI 输入长度
            return text[:8000]

        except requests.exceptions.RequestException as e:
            print(f"    [网页访问失败] {e}")
            return None


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
    db = ClipboardDB()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("=" * 60)
    print("第 1 步：写入 3 条测试数据")
    print("=" * 60)

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
            "content": "这是一段需要保存的文本内容。",
            "title": "备忘文本",
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
    ]

    for data in sample_data_list:
        record_id = db.receive(data)
        print(f"  [OK] 已存入 -> id={record_id} | {data['title']}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "=" * 60)
    print("第 2 步：结构化查询 — 各种条件搜一搜")
    print("=" * 60)

    engine = QueryEngine(db)

    # 演示1：按关键词搜
    print("\n>> 搜关键词 'Python'：")
    results = engine.search(keywords=["Python"])
    for r in results:
        print(f"    #{r['id']} [{r['content_type']}] {r['title']} — {r['content'][:60]}")

    # 演示2：只搜网址类型
    print("\n>> 只看网址类型 (url)：")
    results = engine.search(content_type="url", limit=10)
    for r in results:
        print(f"    #{r['id']} [{r['origin']}] {r['title']} — {r['content'][:60]}")

    # 演示3：按来源搜
    print("\n>> 只看浏览器历史 (browser_history)：")
    results = engine.search(origin=ORIGIN_BROWSER_HISTORY)
    for r in results:
        print(f"    #{r['id']} [{r['content_type']}] {r['title']} — {r['content'][:60]}")

    # 演示4：多条件组合
    print("\n>> 组合条件 — 关键词 'Python' + 网址类型 + 7月1日之后：")
    results = engine.search(
        keywords=["Python"],
        content_type="url",
        date_from="2026-07-01",
        limit=10,
    )
    for r in results:
        print(f"    #{r['id']} {r['title']}")
    if not results:
        print("    (没有符合条件的记录)")

    # 演示5：看总数
    total = engine.count()
    print(f"\n>> 数据库里总共有 {total} 条记录")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    print("\n" + "=" * 60)
    print("第 3 步：自然语言查询 — 用大白话提问（AI 还没接）")
    print("=" * 60)

    print("\n提问 1：「找一下 Python 相关的内容」")
    print("  (AI 未接入，自动用土法提取关键词)")
    results = ask("找一下 Python 相关的内容", db)
    if results:
        for r in results:
            print(f"    -> #{r['id']} [{r['content_type']}] {r['title']}")
    else:
        print("    (没找到)")

    print("\n提问 2：「帮我查找浏览器历史里面的 Linux」")
    results = ask("帮我查找浏览器历史里面的 Linux", db)
    if results:
        for r in results:
            print(f"    -> #{r['id']} [{r['content_type']}] {r['title']} | 来源={r['origin']}")
    else:
        print("    (没找到)")

    db.close()
    print("\n" + "=" * 60)
    print("测试完毕！数据库和代码在同一个文件夹里。")
