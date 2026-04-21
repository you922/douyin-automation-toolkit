"""诊断脚本：探查抖音图文发布页面的上传区域 DOM 结构。

运行前请确保：
1. Chrome 已以 --remote-debugging-port=9222 启动
2. 已打开抖音图文发布页面
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))

from douyin.cdp import Browser

def main():
    browser = Browser()
    page = browser.get_existing_page()
    if not page:
        print("未找到已打开的页面")
        return

    url = page.get_current_url()
    print(f"当前页面: {url}")

    # 如果不在上传页，先导航过去
    upload_url = "https://creator.douyin.com/creator-micro/content/upload?default-tab=3"
    if "upload" not in url:
        print(f"导航到上传页: {upload_url}")
        page.navigate(upload_url)
        page.wait_for_load()
        import time; time.sleep(3)

    # 1. 查找所有 input 元素
    result = page.evaluate("""
    (() => {
        const inputs = document.querySelectorAll('input');
        return Array.from(inputs).map(el => ({
            type: el.type,
            accept: el.accept,
            name: el.name,
            id: el.id,
            className: el.className.substring(0, 80),
            style: el.getAttribute('style') || '',
            visible: el.getBoundingClientRect().width > 0,
        }));
    })()
    """)
    print(f"\n=== 所有 input 元素 ({len(result)} 个) ===")
    for inp in result:
        print(inp)

    # 2. 查找 phone-container 类元素
    result2 = page.evaluate("""
    (() => {
        const all = document.querySelectorAll('[class]');
        const found = [];
        for (const el of all) {
            const classes = Array.from(el.classList);
            if (classes.some(c => c.startsWith('phone-container'))) {
                const rect = el.getBoundingClientRect();
                found.push({
                    tag: el.tagName,
                    className: el.className.substring(0, 80),
                    width: rect.width,
                    height: rect.height,
                    childCount: el.children.length,
                    innerHTML: el.innerHTML.substring(0, 200),
                });
            }
        }
        return found;
    })()
    """)
    print(f"\n=== phone-container 元素 ({len(result2)} 个) ===")
    for el in result2:
        print(el)

    # 3. 查找含「点击上传」文案的元素
    result3 = page.evaluate("""
    (() => {
        const all = document.querySelectorAll('*');
        const found = [];
        for (const el of all) {
            if (el.children.length === 0 && el.textContent.includes('点击上传')) {
                const rect = el.getBoundingClientRect();
                found.push({
                    tag: el.tagName,
                    className: el.className.substring(0, 80),
                    text: el.textContent.trim().substring(0, 50),
                    width: rect.width,
                    height: rect.height,
                    parentTag: el.parentElement?.tagName,
                    parentClass: el.parentElement?.className.substring(0, 80),
                });
            }
        }
        return found;
    })()
    """)
    print(f"\n=== 含「点击上传」文案的叶子元素 ({len(result3)} 个) ===")
    for el in result3:
        print(el)

    # 4. 查找 bold-text-container 类元素
    result4 = page.evaluate("""
    (() => {
        const all = document.querySelectorAll('[class]');
        const found = [];
        for (const el of all) {
            const classes = Array.from(el.classList);
            if (classes.some(c => c.startsWith('bold-text-container'))) {
                const rect = el.getBoundingClientRect();
                found.push({
                    tag: el.tagName,
                    className: el.className.substring(0, 80),
                    text: el.textContent.trim().substring(0, 80),
                    width: rect.width,
                    height: rect.height,
                });
            }
        }
        return found;
    })()
    """)
    print(f"\n=== bold-text-container 元素 ({len(result4)} 个) ===")
    for el in result4:
        print(el)

    # 5. 查找上传区域附近的结构（含 upload 关键词的 class）
    result5 = page.evaluate("""
    (() => {
        const all = document.querySelectorAll('[class]');
        const found = [];
        for (const el of all) {
            const cls = el.className.toLowerCase();
            if (cls.includes('upload') || cls.includes('uploader')) {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0) {
                    found.push({
                        tag: el.tagName,
                        className: el.className.substring(0, 100),
                        width: rect.width,
                        height: rect.height,
                        text: el.textContent.trim().substring(0, 60),
                    });
                }
            }
        }
        return found;
    })()
    """)
    print(f"\n=== 含 upload/uploader class 的可见元素 ({len(result5)} 个) ===")
    for el in result5:
        print(el)

if __name__ == "__main__":
    main()
