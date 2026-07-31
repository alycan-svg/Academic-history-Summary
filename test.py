import time
from collector_package import Collector

def run_test():
    # 1. 初始化类
    c = Collector()
    print("\n--- 🕵️ 采集引擎实战测试启动 ---")
    print("测试步骤：")
    print("1. 复制一段文字。")
    print("2. 紧接着在浏览器打开一个 arXiv 或 知乎 链接。")
    print("3. 回来看控制台是否同时抓取到这两条记录。\n")

    try:
        while True:
            # 2. 调用接口获取列表
            new_items = c.get_data()

            # 3. 遍历列表（如果是空的，循环会自动跳过）
            for item in new_items:
                print(f"✨ [抓取成功] 来源: {item['origin']} | 标题: {item['title']}")
                print(f"   内容: {item['content'][:60]}...") # 只打印前60位
            
            # 4. 每隔2秒轮询一次
            time.sleep(2)

    except KeyboardInterrupt:
        print("\n测试已安全结束。")

if __name__ == "__main__":
    run_test()