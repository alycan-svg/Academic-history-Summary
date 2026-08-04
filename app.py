# ============================================================
# app.py — 摘要浏览器 Web 后端（Flask）
#
# 这是 GUI.py 的网页化版本：把原来 tkinter 里每个功能，
# 对应改成一个 REST 接口，供前端页面调用。
#
# 依赖：
#   pip install flask
#   同目录下需要有 search.py（数据库 + AI 后端，与原项目一致）
#
# 启动：
#   python app.py
#   然后浏览器打开 http://127.0.0.1:5000
# ============================================================

import threading

from flask import Flask, jsonify, request, render_template

from search import (
    ClipboardDB,
    UserSummaryDB,
    LightweightSummarizer,
    AISummarizer,
    QueryEngine,
    UFIELD_ID,
    UFIELD_SOURCE_ID,
    UFIELD_TITLE,
    UFIELD_SUMMARY,
    UFIELD_TAGS,
    UFIELD_DEEP_SUMMARY,
    UFIELD_IS_DEEP_SUMMARIZED,
    UFIELD_CREATED_AT,
    FIELD_CONTENT_TYPE,
    FIELD_CONTENT,
    FIELD_ORIGIN,
)

app = Flask(__name__)

# ── 初始化数据库 / AI 模块（与原 GUI.py 的 __main__ 部分一致）──────
db = ClipboardDB()
user_db = UserSummaryDB()
light_sum = LightweightSummarizer()
deep_sum = AISummarizer()

db.user_db = user_db
db.light_summarizer = light_sum

source_query = QueryEngine(db)

# 深度摘要是后台线程任务，这里用一个内存字典记录任务状态，
# 供前端轮询："idle" | "running" | "done" | "error"
_deep_jobs_lock = threading.Lock()
_deep_jobs = {}


def _record_to_json(r: dict) -> dict:
    """把数据库记录转换成前端需要的 JSON 结构"""
    return {
        "id": r[UFIELD_ID],
        "source_id": r[UFIELD_SOURCE_ID],
        "title": r[UFIELD_TITLE] or "(无标题)",
        "summary": r[UFIELD_SUMMARY] or "",
        "tags": [t.strip() for t in (r[UFIELD_TAGS] or "").split(",") if t.strip()],
        "deep_summary": r[UFIELD_DEEP_SUMMARY] or "",
        "is_deep_summarized": bool(r[UFIELD_IS_DEEP_SUMMARIZED]),
        "created_at": r[UFIELD_CREATED_AT] or "",
    }


# ────────────────────────────────────────────────────────────
# 页面
# ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ────────────────────────────────────────────────────────────
# API：记录列表（对应原来的 Treeview + 搜索/标签/排序）
# ────────────────────────────────────────────────────────────

@app.route("/api/records")
def api_records():
    search_kw = request.args.get("q", "").strip()
    tag = request.args.get("tag", "").strip()
    pending_only = request.args.get("pending", "") == "1"
    sort_col = request.args.get("sort", "created_at")
    sort_desc = request.args.get("order", "desc") != "asc"
    page = max(1, int(request.args.get("page", 1)))
    page_size = min(100, max(1, int(request.args.get("page_size", 24))))

    kwargs = {}
    if search_kw:
        kwargs["keywords"] = search_kw
    if tag:
        kwargs["tags"] = tag
    if pending_only:
        kwargs["is_deep_summarized"] = 0

    total = user_db.count(
        keywords=kwargs.get("keywords"),
        tags=kwargs.get("tags"),
        is_deep_summarized=kwargs.get("is_deep_summarized"),
    )

    order_by = f"{sort_col} {'DESC' if sort_desc else 'ASC'}"

    # 注意：原 UserSummaryDB.search 只暴露了 limit，没有 offset，
    # 这里在后端内存里做分页切片，保证不依赖数据库层是否支持 offset。
    fetch_limit = min(total, 5000) if total else 0
    all_records = user_db.search(order_by=order_by, limit=fetch_limit, **kwargs) if fetch_limit else []

    start = (page - 1) * page_size
    page_records = all_records[start:start + page_size]

    return jsonify({
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": start + page_size < total,
        "records": [_record_to_json(r) for r in page_records],
    })


@app.route("/api/tags")
def api_tags():
    """所有标签去重列表，用于筛选下拉框"""
    tag_set = set()
    try:
        total = user_db.count()
        all_records = user_db.search(limit=min(total, 5000)) if total else []
        for r in all_records:
            for t in (r[UFIELD_TAGS] or "").split(","):
                t = t.strip()
                if t:
                    tag_set.add(t)
    except Exception:
        pass
    return jsonify({"tags": sorted(tag_set)})


# ────────────────────────────────────────────────────────────
# API：详情（对应原来的详情弹窗）
# ────────────────────────────────────────────────────────────

@app.route("/api/records/<int:summary_id>")
def api_record_detail(summary_id):
    record = user_db.get_by_id(summary_id)
    if not record:
        return jsonify({"error": "记录不存在"}), 404

    source = source_query.get_by_id(record[UFIELD_SOURCE_ID])
    data = _record_to_json(record)

    if source:
        data["source"] = {
            "content_type": source[FIELD_CONTENT_TYPE],
            "content": source[FIELD_CONTENT],
            "origin": source[FIELD_ORIGIN],
        }
    else:
        data["source"] = None

    with _deep_jobs_lock:
        data["deep_job_status"] = _deep_jobs.get(summary_id, "idle")

    return jsonify(data)


# ────────────────────────────────────────────────────────────
# API：触发 / 查询深度摘要（对应原来的后台线程逻辑）
# ────────────────────────────────────────────────────────────

@app.route("/api/records/<int:summary_id>/deep-summary", methods=["POST"])
def api_trigger_deep_summary(summary_id):
    record = user_db.get_by_id(summary_id)
    if not record:
        return jsonify({"error": "记录不存在"}), 404

    if record[UFIELD_IS_DEEP_SUMMARIZED]:
        return jsonify({"status": "done", "deep_summary": record[UFIELD_DEEP_SUMMARY]})

    source = source_query.get_by_id(record[UFIELD_SOURCE_ID])
    if not source:
        return jsonify({"error": "无法找到原始记录"}), 400

    with _deep_jobs_lock:
        if _deep_jobs.get(summary_id) == "running":
            return jsonify({"status": "running"})
        _deep_jobs[summary_id] = "running"

    def _work():
        try:
            result = deep_sum.summarize(source)
            text = result.get("ai_summary", str(result))
            user_db.update_deep_summary(summary_id, text)
            with _deep_jobs_lock:
                _deep_jobs[summary_id] = "done"
        except Exception as e:
            with _deep_jobs_lock:
                _deep_jobs[summary_id] = "error"
            app.logger.exception("深度摘要生成失败: %s", e)

    threading.Thread(target=_work, daemon=True).start()
    return jsonify({"status": "running"})


@app.route("/api/records/<int:summary_id>/deep-summary/status")
def api_deep_summary_status(summary_id):
    with _deep_jobs_lock:
        status = _deep_jobs.get(summary_id, "idle")
    if status == "done":
        record = user_db.get_by_id(summary_id)
        return jsonify({
            "status": "done",
            "deep_summary": record[UFIELD_DEEP_SUMMARY] if record else "",
        })
    return jsonify({"status": status})


# ────────────────────────────────────────────────────────────
# API：统计（对应原来的统计弹窗，这里改成图表数据）
# ────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    total = user_db.count()
    deep_done = user_db.count(is_deep_summarized=1)
    deep_pending = user_db.count(is_deep_summarized=0)

    timeline = {}
    tag_freq = {}
    try:
        recent = user_db.search(order_by="created_at DESC", limit=min(total, 2000)) if total else []
        for r in recent:
            date = (r[UFIELD_CREATED_AT] or "")[:10]
            if date:
                timeline[date] = timeline.get(date, 0) + 1
            for t in (r[UFIELD_TAGS] or "").split(","):
                t = t.strip()
                if t:
                    tag_freq[t] = tag_freq.get(t, 0) + 1
    except Exception:
        pass

    timeline_sorted = sorted(timeline.items())[-30:]
    tag_sorted = sorted(tag_freq.items(), key=lambda x: -x[1])[:10]

    return jsonify({
        "total": total,
        "deep_done": deep_done,
        "deep_pending": deep_pending,
        "timeline": [{"date": d, "count": c} for d, c in timeline_sorted],
        "top_tags": [{"tag": t, "count": c} for t, c in tag_sorted],
    })


if __name__ == "__main__":
    print("=" * 60)
    print("用户摘要浏览器 — 网页版")
    print("=" * 60)
    print("浏览器打开 http://127.0.0.1:5000 即可使用")
    app.run(debug=True, port=5000)
