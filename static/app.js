// ============================================================
// app.js — 摘要档案馆前端逻辑
// 对应原 GUI.py 的每个功能：
//   _refresh_list      → loadRecords()
//   _do_search         → 搜索框
//   _filter_by_tag     → 标签下拉
//   _sort_by           → 排序控件
//   _show_stats        → loadStats()（图表版）
//   _open_detail       → openDetail()
//   _run_deep_summary_thread / _on_deep_done → triggerDeepSummary() + 轮询
// ============================================================

const state = {
  q: "",
  tag: "",
  pending: false,
  sort: "created_at",
  order: "desc",
  page: 1,
  pageSize: 24,
};

const els = {
  search: document.getElementById("search-input"),
  tagSelect: document.getElementById("tag-select"),
  pendingToggle: document.getElementById("pending-toggle"),
  sortSelect: document.getElementById("sort-select"),
  orderBtn: document.getElementById("order-btn"),
  resetBtn: document.getElementById("reset-btn"),
  statusLine: document.getElementById("status-line"),
  cardGrid: document.getElementById("card-grid"),
  emptyState: document.getElementById("empty-state"),
  prevPage: document.getElementById("prev-page"),
  nextPage: document.getElementById("next-page"),
  pageIndicator: document.getElementById("page-indicator"),
  overlay: document.getElementById("detail-overlay"),
  drawerContent: document.getElementById("drawer-content"),
  drawerClose: document.getElementById("drawer-close"),
};

// ── Tab 切换 ─────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`view-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "stats") loadStats();
  });
});

// ── 目录：加载记录 ───────────────────────────────────────

async function loadRecords() {
  els.statusLine.textContent = "加载中…";
  const params = new URLSearchParams({
    q: state.q,
    tag: state.tag,
    pending: state.pending ? "1" : "0",
    sort: state.sort,
    order: state.order,
    page: state.page,
    page_size: state.pageSize,
  });

  try {
    const res = await fetch(`/api/records?${params}`);
    const data = await res.json();
    renderCards(data.records);
    els.statusLine.textContent = `共 ${data.total} 条记录 · 第 ${data.page} 页`;
    els.pageIndicator.textContent = `第 ${data.page} 页`;
    els.prevPage.disabled = data.page <= 1;
    els.nextPage.disabled = !data.has_more;
  } catch (err) {
    els.statusLine.textContent = "加载失败，请检查后端服务是否已启动";
    console.error(err);
  }
}

function renderCards(records) {
  els.cardGrid.innerHTML = "";
  els.emptyState.hidden = records.length > 0;

  records.forEach(r => {
    const card = document.createElement("div");
    card.className = "record-card";
    card.innerHTML = `
      <span class="card-stamp ${r.is_deep_summarized ? "done" : "pending"}">
        ${r.is_deep_summarized ? "✓" : "…"}
      </span>
      <h3 class="card-title">${escapeHtml(r.title)}</h3>
      <p class="card-summary">${escapeHtml(r.summary || "（暂无摘要）")}</p>
      <div class="card-tags">
        ${r.tags.map(t => `<span class="tag-chip">${escapeHtml(t)}</span>`).join("")}
      </div>
      <span class="card-date">${escapeHtml(r.created_at.slice(0, 10))}</span>
    `;
    card.addEventListener("click", () => openDetail(r.id));
    els.cardGrid.appendChild(card);
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// ── 筛选控件事件 ─────────────────────────────────────────

let searchDebounce;
els.search.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.q = els.search.value.trim();
    state.page = 1;
    loadRecords();
  }, 350);
});

els.tagSelect.addEventListener("change", () => {
  state.tag = els.tagSelect.value;
  state.page = 1;
  loadRecords();
});

els.pendingToggle.addEventListener("change", () => {
  state.pending = els.pendingToggle.checked;
  state.page = 1;
  loadRecords();
});

els.sortSelect.addEventListener("change", () => {
  state.sort = els.sortSelect.value;
  loadRecords();
});

els.orderBtn.addEventListener("click", () => {
  state.order = state.order === "desc" ? "asc" : "desc";
  els.orderBtn.textContent = state.order === "desc" ? "↓" : "↑";
  loadRecords();
});

els.resetBtn.addEventListener("click", () => {
  state.q = ""; state.tag = ""; state.pending = false;
  state.sort = "created_at"; state.order = "desc"; state.page = 1;
  els.search.value = ""; els.tagSelect.value = ""; els.pendingToggle.checked = false;
  els.sortSelect.value = "created_at"; els.orderBtn.textContent = "↓";
  loadRecords();
});

els.prevPage.addEventListener("click", () => {
  if (state.page > 1) { state.page -= 1; loadRecords(); }
});
els.nextPage.addEventListener("click", () => {
  state.page += 1; loadRecords();
});

// ── 标签下拉填充 ─────────────────────────────────────────

async function loadTags() {
  try {
    const res = await fetch("/api/tags");
    const data = await res.json();
    data.tags.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t; opt.textContent = t;
      els.tagSelect.appendChild(opt);
    });
  } catch (err) { console.error(err); }
}

// ── 详情抽屉 ─────────────────────────────────────────────

async function openDetail(id) {
  els.overlay.classList.add("open");
  els.drawerContent.innerHTML = `<p class="deep-placeholder">加载中…</p>`;

  const res = await fetch(`/api/records/${id}`);
  const r = await res.json();
  renderDetail(r);
}

function renderDetail(r) {
  const source = r.source;
  let mediaHtml = `<p class="deep-placeholder">（无法获取原始内容）</p>`;

  if (source) {
    const type = (source.content_type || "").toLowerCase();
    const content = source.content || "";
    if (type.includes("image")) {
      mediaHtml = `<div class="drawer-media"><img src="${escapeHtml(content)}" alt="原始图片"></div>`;
    } else if (type.includes("video")) {
      mediaHtml = `<div class="drawer-media"><video src="${escapeHtml(content)}" controls></video></div>`;
    } else if (type.includes("url") || type.includes("link")) {
      mediaHtml = `<a class="drawer-link" href="${escapeHtml(content)}" target="_blank" rel="noopener">${escapeHtml(content)}</a>`;
    } else {
      mediaHtml = `<pre>${escapeHtml(content)}</pre>`;
    }
  }

  const deepBody = r.is_deep_summarized
    ? `<p>${escapeHtml(r.deep_summary)}</p>`
    : `<p class="deep-placeholder" id="deep-text">尚未生成深度摘要</p>
       <button class="deep-btn" id="deep-btn">🤖 生成 AI 深度总结</button>`;

  els.drawerContent.innerHTML = `
    <h2>${escapeHtml(r.title)}</h2>

    <div class="drawer-section">
      <h4>基本信息</h4>
      <p><strong>摘要：</strong>${escapeHtml(r.summary || "（无）")}</p>
      <p><strong>标签：</strong>${r.tags.length ? r.tags.map(escapeHtml).join(", ") : "（无）"}</p>
      <p><strong>创建日期：</strong>${escapeHtml(r.created_at)}</p>
      ${source ? `<p><strong>来源：</strong>${escapeHtml(source.origin || "")} (${escapeHtml(source.content_type || "")})</p>` : ""}
    </div>

    <div class="drawer-section">
      <h4>原始内容</h4>
      ${mediaHtml}
    </div>

    <div class="drawer-section">
      <h4>深度 AI 摘要</h4>
      ${deepBody}
    </div>
  `;

  if (!r.is_deep_summarized) {
    document.getElementById("deep-btn").addEventListener("click", () => triggerDeepSummary(r.id));
  }
}

els.drawerClose.addEventListener("click", () => els.overlay.classList.remove("open"));
els.overlay.addEventListener("click", (e) => {
  if (e.target === els.overlay) els.overlay.classList.remove("open");
});

// ── 深度摘要：触发 + 轮询 ────────────────────────────────

async function triggerDeepSummary(id) {
  const btn = document.getElementById("deep-btn");
  const text = document.getElementById("deep-text");
  btn.disabled = true;
  btn.textContent = "⏳ 正在生成…";
  text.textContent = "AI 正在阅读原始内容并生成深度摘要，请稍候…";

  try {
    await fetch(`/api/records/${id}/deep-summary`, { method: "POST" });
  } catch (err) {
    text.textContent = "请求失败，请重试";
    btn.disabled = false;
    btn.textContent = "🤖 生成 AI 深度总结";
    return;
  }

  const poll = setInterval(async () => {
    const res = await fetch(`/api/records/${id}/deep-summary/status`);
    const data = await res.json();
    if (data.status === "done") {
      clearInterval(poll);
      text.outerHTML = `<p>${escapeHtml(data.deep_summary)}</p>`;
      btn.remove();
      loadRecords(); // 刷新列表中的印章状态
    } else if (data.status === "error") {
      clearInterval(poll);
      text.textContent = "生成失败，请重试";
      btn.disabled = false;
      btn.textContent = "🤖 生成 AI 深度总结";
    }
  }, 1500);
}

// ── 统计视图：图表 ───────────────────────────────────────

let donutChart, timelineChart, tagChart;

async function loadStats() {
  const res = await fetch("/api/stats");
  const data = await res.json();

  document.getElementById("stat-total").textContent = data.total;
  document.getElementById("stat-done").textContent = data.deep_done;
  document.getElementById("stat-pending").textContent = data.deep_pending;

  const moss = "#3F5D46", rust = "#A6402F", amber = "#C08A2E", line = "#DCD3BE";

  if (donutChart) donutChart.destroy();
  donutChart = new Chart(document.getElementById("chart-donut"), {
    type: "doughnut",
    data: {
      labels: ["已深度总结", "待深度总结"],
      datasets: [{ data: [data.deep_done, data.deep_pending], backgroundColor: [moss, rust], borderWidth: 0 }],
    },
    options: {
      plugins: { legend: { position: "bottom", labels: { font: { family: "IBM Plex Mono", size: 11 } } } },
    },
  });

  if (timelineChart) timelineChart.destroy();
  timelineChart = new Chart(document.getElementById("chart-timeline"), {
    type: "line",
    data: {
      labels: data.timeline.map(p => p.date.slice(5)),
      datasets: [{
        label: "新增记录",
        data: data.timeline.map(p => p.count),
        borderColor: amber, backgroundColor: "rgba(192,138,46,0.15)",
        fill: true, tension: 0.3, pointRadius: 2,
      }],
    },
    options: {
      scales: {
        x: { grid: { color: line }, ticks: { font: { family: "IBM Plex Mono", size: 10 } } },
        y: { grid: { color: line }, beginAtZero: true, ticks: { precision: 0 } },
      },
      plugins: { legend: { display: false } },
    },
  });

  if (tagChart) tagChart.destroy();
  tagChart = new Chart(document.getElementById("chart-tags"), {
    type: "bar",
    data: {
      labels: data.top_tags.map(t => t.tag),
      datasets: [{ data: data.top_tags.map(t => t.count), backgroundColor: moss, borderRadius: 4 }],
    },
    options: {
      indexAxis: "y",
      scales: {
        x: { grid: { color: line }, beginAtZero: true, ticks: { precision: 0 } },
        y: { grid: { display: false }, ticks: { font: { family: "IBM Plex Mono", size: 11 } } },
      },
      plugins: { legend: { display: false } },
    },
  });
}

// ── 初始化 ───────────────────────────────────────────────

loadTags();
loadRecords();
