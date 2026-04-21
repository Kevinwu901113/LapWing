from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    
    page.goto("https://www.xiaohongshu.com/login", timeout=30000)
    time.sleep(5)  # 等待页面加载
    
    # 截图
    page.screenshot(path="/home/kevin/lapwing/login_page.png", full_page=False)
    browser.close()
    print("截图已保存: /home/kevin/lapwing/login_page.png")