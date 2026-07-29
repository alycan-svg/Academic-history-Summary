import pyperclip
import sqlite3
import shutil
import os
import time
import re
from datetime import datetime

import os
import platform

def get_chrome_history_path():
    """
    动态获取 Chrome 历史记录路径，适配不同系统
    """
    if platform.system() == "Windows":
        # Windows 的标准路径
        base_path = os.path.expanduser(r"C:\Users\Administrator\AppData\Local\Google\Chrome\User Data\Profile 1\History")
        
        # 特别提醒：如果你在 Chrome 中登录了多个账号，文件夹可能不是 "Default"
        # 而是 "Profile 1", "Profile 2" 等。如果 Default 不存在，可以手动去该目录下确认。
        if not os.path.exists(base_path):
            # 备选方案：尝试检查其他 Profile（此处仅为提示，通常 Default 即可）
            pass 
        return base_path
    
    elif platform.system() == "Darwin": # Mac 系统
        return os.path.expanduser("~/Library/Application Support/Google/Chrome/Default/History")
    
    else: # Linux 系统
        return os.path.expanduser("~/.config/google-chrome/Default/History")

# 在你的配置区使用它
HISTORY_PATH = get_chrome_history_path()

# --- [配置区] ---
# 1. 浏览器历史记录路径 (以 Edge 为例，Chrome 类似)
#HISTORY_PATH = os.path.expanduser(r"~\AppData\Local\Microsoft\Chrome\User Data\Default\History")

# 2. 采集白名单 (只采集感兴趣的网站，防止垃圾数据)
WHITE_LIST = ["arxiv.org", "zhihu.com", "github.com", "bilibili.com", "csdn.net", "v.sjtu.edu.cn", "openai.com"]

# 3. URL 识别正则
URL_PATTERN = r'^https?://[^\s]+'

# --- [核心功能模块] ---

def package_data(content, title, c_type, origin):
    """
    统一打包协议
    """
    return {
        "type": c_type,             # "url" 或 "text"
        "content": content,         # URL 链接或纯文本内容
        "title": title,             # 网页标题或文本简述
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "origin": origin,           # 来源标记："clipboard" 或 "browser_history"
        "processed": False          # 留给 B 同学的初始状态
    }

def get_latest_history():
    """读取浏览器数据库的最新一条记录"""
    temp_db = "temp_history.db"
    try:
        shutil.copyfile(HISTORY_PATH, temp_db)
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        query = "SELECT url, title, last_visit_time FROM urls ORDER BY last_visit_time DESC LIMIT 1"
        cursor.execute(query)
        res = cursor.fetchone()
        conn.close()
        if os.path.exists(temp_db): os.remove(temp_db)
        return res # (url, title, visit_time)
    except:
        return None

def is_url(text):
    return re.match(URL_PATTERN, text) is not None

def main():
    print("🛰️  Paper-Miner 双模采集引擎已启动...")
    
    # 状态追踪：
    last_clipboard = pyperclip.paste().strip()
    
    # 获取初始历史记录，设置初始时间戳
    initial_history = get_latest_history()
    last_visit_time = initial_history[2] if initial_history else 0
    
    # --- 新增：记录最后一次真正【输出】的 URL ---
    last_captured_url = "" 

    try:
        while True:
            # --- 1. 剪切板监听逻辑 ---
            curr_clipboard = pyperclip.paste().strip()
            if curr_clipboard != last_clipboard and curr_clipboard != "":
                c_type = "url" if is_url(curr_clipboard) else "text"
                
                # 只有当剪切板内容和上一次捕获的内容不同时才输出
                if curr_clipboard != last_captured_url:
                    data_item = package_data(curr_clipboard, "剪切板捕获内容", c_type, "clipboard")
                    print(f"\n[剪切板响应] 捕获新内容...")
                    print(f"数据包: {data_item}")
                    
                    last_captured_url = curr_clipboard # 更新最后捕获记录
                
                last_clipboard = curr_clipboard

            # --- 2. 浏览器监听逻辑 ---
            history = get_latest_history()
            if history:
                url, title, visit_time = history
                
                # 首先判断时间戳：是否是新的访问行为？
                if visit_time > last_visit_time:
                    # 更新时间戳标记位
                    last_visit_time = visit_time
                    
                    # 其次判断白名单
                    if any(domain in url for domain in WHITE_LIST):
                        # --- 核心优化：判断 URL 是否与上一次捕获的一样 ---
                        # 这样可以防止你刷新网页或者在短时间内反复点进同一个网页产生重复数据
                        if url != last_captured_url:
                            data_item = package_data(url, title, "url", "browser_history")
                            print(f"\n[浏览器响应] 发现目标网页: {title}")
                            print(f"数据包: {data_item}")
                            
                            last_captured_url = url # 更新最后捕获记录
            
            time.sleep(2) 
            
    except KeyboardInterrupt:
        print("\n引擎已关闭。")

if __name__ == "__main__":
    main()