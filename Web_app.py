import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from search import UserSummaryDB, ClipboardDB, AISummarizer

# --- 页面配置 ---
st.set_page_config(page_title="Academic-history-Summary", layout="wide")

# --- 初始化后台（与你之前的逻辑对接） ---
@st.cache_resource
def init_resources():
    user_db = UserSummaryDB()
    source_db = ClipboardDB()
    summarizer = AISummarizer()
    return user_db, source_db, summarizer

user_db, source_db, summarizer = init_resources()

# --- 侧边栏：过滤与搜索 ---
st.sidebar.header("🔍 过滤器")
search_query = st.sidebar.text_input("搜索关键词")
tag_filter = st.sidebar.selectbox("标签过滤", ["全部"] + ["Python", "北航", "论文", "AI"]) # 这里可从数据库提取

# --- 主页面布局 ---
st.title("🎓 学术与社交碎片管理系统")
st.markdown("---")

# --- 模块一：可视化看板 (新功能) ---
st.subheader("📊 数据概览")
col1, col2, col3 = st.columns(3)

# 获取统计数据
total_count = user_db.count()
deep_count = user_db.count(is_deep_summarized=1)

with col1:
    st.metric("总记录数", total_count)
with col2:
    st.metric("已深度分析", deep_count)
with col3:
    st.progress(deep_count / total_count if total_count > 0 else 0)
    st.write("分析完成率")

# 图表展示
chart_col1, chart_col2 = st.columns(2)
with chart_col1:
    # 示例：来源分布饼图 (需要从数据库查 origin)
    df_stats = pd.DataFrame({
        "来源": ["剪切板", "浏览器历史"],
        "数量": [120, 85] # 实际从 db.count(origin=...) 获取
    })
    fig = px.pie(df_stats, values='数量', names='来源', title="数据来源分布", hole=0.3)
    st.plotly_chart(fig, use_container_width=True)

with chart_col2:
    # 示例：近期活跃折线图
    df_line = pd.DataFrame({
        "日期": pd.date_range(start="2024-07-01", periods=7),
        "记录数": [5, 8, 12, 7, 15, 10, 9]
    })
    fig_line = px.line(df_line, x="日期", y="记录数", title="近期捕获趋势")
    st.plotly_chart(fig_line, use_container_width=True)

# --- 模块二：瀑布流卡片展示 (替代 Treeview) ---
st.subheader("📑 知识记录列表")

# 获取数据
records = user_db.search(keywords=search_query, limit=20)

# 使用卡片式布局
for r in records:
    with st.container():
        c1, c2 = st.columns([0.8, 0.2])
        with c1:
            st.markdown(f"### {r['title'] or '无标题'}")
            st.caption(f"📅 {r['created_at']} | 🏷️ {r['tags']}")
            st.write(r['summary'])
        with c2:
            if st.button("查看详情", key=f"btn_{r['id']}"):
                st.session_state.detail_id = r['id']
                st.rerun() # 模拟页面跳转
        st.divider()

# --- 模块三：详情视图 (通过 Session State 控制) ---
if 'detail_id' in st.session_state:
    st.sidebar.divider()
    if st.sidebar.button("⬅️ 返回列表"):
        del st.session_state.detail_id
        st.rerun()
    
    # 这里展示具体的详细总结、原文图片或链接视频预览
    st.info(f"正在查看详情 ID: {st.session_state.detail_id}")
    # 可以调用 AI 生成深度总结并显示