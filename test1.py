{
        "type": c_type,             # "url" 或 "text"
        "content": content,         # URL 链接或纯文本内容
        "title": title,             # 网页标题或文本简述
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "origin": origin,           # 来源标记："clipboard" 或 "browser_history"
        "processed": False          # 留给 B 同学的初始状态
    }