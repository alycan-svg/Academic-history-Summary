import pyperclip
import sqlite3
import shutil
import os
import time
import re
from datetime import datetime

import os
import platform

class Collector:
    def __init__(self):
        """
        初始化：建立自己的“记忆”，防止重复采集
        """
        self.history_path = self._get_chrome_history_path()
        self.white_list = ["arxiv.org", "zhihu.com", "github.com", "bilibili.com", "csdn.net", "v.sjtu.edu.cn", "openai.com"]
        self.last_clipboard = pyperclip.paste().strip()
        res = self._get_latest_history_raw()
        self.last_visit_time = res[2] if res else 0
        self.last_url = ""
        self.URL_PATTERN = r'^https?://[^\s]+'
        print("✅ 采集引擎初始化完成")
    

    def _get_chrome_history_path(self):
    #动态获取 Chrome 历史记录路径，适配不同系统
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
    

    def _get_latest_history_raw(self):
        """读取浏览器数据库的最新一条记录"""
        temp_db = "temp_history.db"
        try:
            shutil.copyfile(self.history_path, temp_db)
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
        

    def is_url(self,text):
        return re.match(self.URL_PATTERN, text) is not None


    def get_data(self):

        new_items = []

        # --- 1. 剪切板监听逻辑 ---
        curr_clipboard = pyperclip.paste().strip()
        if curr_clipboard != self.last_clipboard and curr_clipboard != "":
            c_type = "url" if self.is_url(curr_clipboard) else "text"
                            # 只有当剪切板内容和上一次捕获的内容不同时才输出
            if curr_clipboard != self.last_url:
                data_item = self.package_data(curr_clipboard, "剪切板捕获内容", c_type, "clipboard")
                print(f"\n[剪切板响应] 捕获新内容...")
                print(f"数据包: {data_item}")
                
                self.last_url = curr_clipboard # 更新最后捕获记录
                self.last_clipboard = curr_clipboard
                new_items.append(data_item)
            else:
                self.last_clipboard = curr_clipboard

        # --- 2. 浏览器监听逻辑 ---
        history = self._get_latest_history_raw()
        if history:
            url, title, visit_time = history
                    
            # 首先判断时间戳：是否是新的访问行为？
            if visit_time > self.last_visit_time:
                # 更新时间戳标记位
                self.last_visit_time = visit_time
                
                # 其次判断白名单
                if any(domain in url for domain in self.white_list):
                    # --- 核心优化：判断 URL 是否与上一次捕获的一样 ---
                    # 这样可以防止你刷新网页或者在短时间内反复点进同一个网页产生重复数据
                    if url != self.last_url:
                        data_item = self.package_data(url, title, "url", "browser_history")
                        print(f"\n[浏览器响应] 发现目标网页: {title}")
                        print(f"数据包: {data_item}")
                        
                        self.last_url = url # 更新最后捕获记录
                        new_items.append(data_item)
                        
        return new_items
        

    def package_data(self,content, title, c_type, origin):
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