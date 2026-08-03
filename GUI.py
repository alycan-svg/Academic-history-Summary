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

from search import (
    QueryEngine,
    AISummarizer,
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


# ============================================================
# 独立启动入口 — 同事拿到 GUI.py 后直接 python GUI.py 即可
# 前提：同目录下有 search.py（数据库 + AI 后端）
# ============================================================

if __name__ == "__main__":
    from search import ClipboardDB, UserSummaryDB, LightweightSummarizer, AISummarizer as DeepSummarizer

    print("=" * 60)
    print("用户摘要浏览器 — Summary Browser GUI")
    print("=" * 60)

    # 初始化数据库和 AI 组件
    print("\n[1] 初始化数据库和 AI 模块...")
    db = ClipboardDB()
    user_db = UserSummaryDB()
    light_sum = LightweightSummarizer()
    deep_sum = DeepSummarizer()

    # 注入自动摘要（ClipboardDB 存入数据时自动调用 LightweightSummarizer）
    db.user_db = user_db
    db.light_summarizer = light_sum

    # 启动 GUI
    print("\n[2] 启动图形界面...")
    app = SummaryBrowserGUI(user_db, db, deep_sum)
    app.launch()

    # 清理
    user_db.close()
    db.close()
    print("\n程序结束。")
