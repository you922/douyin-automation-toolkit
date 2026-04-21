"""文章发布：填写标题/摘要/正文（Markdown）、上传封面、添加话题/音乐，等待用户确认后发布。

通过 CDP 模拟在抖音创作者中心发布文章内容。
发布页面：https://creator.douyin.com/creator-micro/content/post/article
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from .cdp import Page
from .errors import PublishError
from .human import sleep_random
from .urls import PUBLISH_ARTICLE_URL

logger = logging.getLogger(__name__)

# 文章字数限制
ARTICLE_MIN_LENGTH = 100
ARTICLE_MAX_LENGTH = 7000
ARTICLE_TITLE_MAX_LENGTH = 30
ARTICLE_SUMMARY_MAX_LENGTH = 30
ARTICLE_MAX_TOPICS = 5

# 支持的 Markdown 格式（抖音文章编辑器支持的富文本格式）
# 一级~四级标题、加粗、斜体、引用、有序列表、无序列表


def fill_publish_article(
    page: Page,
    title: str,
    content: str,
    summary: str = "",
    cover: str = "",
    topics: list[str] | None = None,
    music: str = "",
    visibility: str = "公开",
) -> dict:
    """填写文章发布表单（不点击发布按钮）。

    Args:
        page: CDP 页面对象。
        title: 文章标题（必填，不超过30字）。
        content: 文章正文（必填，Markdown 格式，100~7000字）。
        summary: 文章摘要（非必填，不超过30字）。
        cover: 封面图片路径（必填）。
        topics: 话题列表（最多5个）。
        music: 背景音乐名称，空字符串表示不选择。
        visibility: 可见范围（公开/好友可见/仅自己可见）。

    Returns:
        操作结果字典，包含 success 字段。
    """
    if not title:
        return {"success": False, "error": "文章标题不能为空"}

    if not content:
        return {"success": False, "error": "文章正文不能为空"}

    content_length = len(content)
    if content_length < ARTICLE_MIN_LENGTH:
        return {
            "success": False,
            "error": f"文章正文不少于 {ARTICLE_MIN_LENGTH} 字，当前 {content_length} 字",
        }
    if content_length > ARTICLE_MAX_LENGTH:
        return {
            "success": False,
            "error": f"文章正文不超过 {ARTICLE_MAX_LENGTH} 字，当前 {content_length} 字",
        }

    if not cover or not os.path.isfile(cover):
        return {"success": False, "error": f"封面图片不存在或未提供: {cover}"}

    topics = topics or []
    if len(topics) > ARTICLE_MAX_TOPICS:
        logger.warning(
            "话题数量 %d 超过限制 %d，截取前 %d 个",
            len(topics),
            ARTICLE_MAX_TOPICS,
            ARTICLE_MAX_TOPICS,
        )
        topics = topics[:ARTICLE_MAX_TOPICS]

    logger.info("导航到文章发布页")
    page.navigate(PUBLISH_ARTICLE_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(4000, 5000)

    # 1. 填写文章标题
    _fill_title(page, title)

    # 2. 填写文章摘要
    if summary:
        _fill_summary(page, summary)

    # 3. 填写文章正文（Markdown 格式）
    _fill_body(page, content)

    # 4. 上传封面
    _upload_cover(page, cover)

    # 5. 添加话题
    if topics:
        _add_topics(page, topics)

    # 6. 选择音乐
    if music:
        _select_music(page, music)

    # 7. 设置可见范围
    _set_visibility(page, visibility)

    logger.info("文章表单填写完成，等待用户确认发布")
    return {"success": True, "message": "表单填写完成，请确认后调用 click-publish 发布"}


def _fill_title(page: Page, title: str) -> None:
    """填写文章标题。"""
    logger.info("填写文章标题: %s", title[:20])

    title_selector = (
        "input[placeholder*='标题'], "
        "input[class*='title'], "
        f"input[maxlength='{ARTICLE_TITLE_MAX_LENGTH}']"
    )
    if page.has_element(title_selector):
        page.input_text(title_selector, title)
        sleep_random(2300, 2500)
    else:
        logger.warning("未找到文章标题输入框，跳过")


def _fill_summary(page: Page, summary: str) -> None:
    """填写文章摘要。"""
    logger.info("填写文章摘要: %s", summary[:20])

    summary_selector = (
        "input[placeholder*='摘要'], "
        "textarea[placeholder*='摘要'], "
        f"input[maxlength='{ARTICLE_SUMMARY_MAX_LENGTH}']"
    )
    if page.has_element(summary_selector):
        page.input_text(summary_selector, summary)
        sleep_random(2300, 2500)
    else:
        logger.warning("未找到摘要输入框，跳过")


def _fill_body(page: Page, content: str) -> None:
    """填写文章正文（Markdown 格式转换为富文本）。

    抖音文章编辑器支持 Markdown 格式输入，通过逐行解析并输入对应格式。
    支持：# ## ### #### 标题、**加粗**、_斜体_、> 引用、有序/无序列表。
    """
    logger.info("填写文章正文: %d 字", len(content))

    # 找到文章正文编辑器
    editor_selector = (
        "div[contenteditable='true'][class*='editor'], "
        "div[contenteditable='true'][class*='article'], "
        "div[role='textbox'][class*='editor']"
    )
    if not page.has_element(editor_selector):
        editor_selector = "div[contenteditable='true']"

    if not page.has_element(editor_selector):
        raise PublishError("未找到文章正文编辑器")

    # 点击编辑器获取焦点
    page.click_element(editor_selector)
    sleep_random(2300, 2500)

    # 逐行处理 Markdown 内容
    lines = content.split("\n")
    for line_index, line in enumerate(lines):
        _input_markdown_line(page, editor_selector, line, line_index == 0)
        if line_index < len(lines) - 1:
            page.press_key("Enter")
            sleep_random(30, 80)

    sleep_random(2500, 2800)
    logger.info("文章正文填写完成")


def _input_markdown_line(
    page: Page, editor_selector: str, line: str, is_first: bool
) -> None:
    """将单行 Markdown 内容输入到编辑器。

    通过键盘快捷键触发对应的富文本格式：
    - # 标题：输入 # 后空格触发
    - **加粗**：Ctrl+B 切换
    - _斜体_：Ctrl+I 切换
    - > 引用：输入 > 后空格触发
    - 1. 有序列表：输入 1. 后空格触发
    - - 无序列表：输入 - 后空格触发
    """
    if not line.strip():
        # 空行直接回车
        return

    # 标题处理（# ## ### ####）
    if line.startswith("####"):
        page.type_text("#### ", delay_ms=30)
        page.type_text(line[4:].strip(), delay_ms=20)
        return
    if line.startswith("###"):
        page.type_text("### ", delay_ms=30)
        page.type_text(line[3:].strip(), delay_ms=20)
        return
    if line.startswith("##"):
        page.type_text("## ", delay_ms=30)
        page.type_text(line[2:].strip(), delay_ms=20)
        return
    if line.startswith("#") and not line.startswith("##"):
        page.type_text("# ", delay_ms=30)
        page.type_text(line[1:].strip(), delay_ms=20)
        return

    # 引用（> 开头）
    if line.startswith("> "):
        page.type_text("> ", delay_ms=30)
        _input_inline_formatted_text(page, line[2:])
        return

    # 有序列表（1. 开头）
    if len(line) > 2 and line[0].isdigit() and line[1] == "." and line[2] == " ":
        page.type_text(f"{line[0]}. ", delay_ms=30)
        _input_inline_formatted_text(page, line[3:])
        return

    # 无序列表（- 开头）
    if line.startswith("- "):
        page.type_text("- ", delay_ms=30)
        _input_inline_formatted_text(page, line[2:])
        return

    # 普通段落（处理行内格式）
    _input_inline_formatted_text(page, line)


def _input_inline_formatted_text(page: Page, text: str) -> None:
    """处理行内 Markdown 格式（加粗、斜体、加粗斜体）。

    支持：**text**（加粗）、_text_（斜体）、***text***（加粗斜体）、**_text_**（加粗斜体）
    """
    import re

    # 匹配行内格式：***text***、**_text_**、**text**、_text_
    pattern = re.compile(
        r"(\*\*\*(.+?)\*\*\*|\*\*_(.+?)_\*\*|_\*\*(.+?)\*\*_|\*\*(.+?)\*\*|_(.+?)_)"
    )

    last_end = 0
    for match in pattern.finditer(text):
        # 输出匹配前的普通文本
        if match.start() > last_end:
            page.type_text(text[last_end : match.start()], delay_ms=20)

        matched = match.group(0)

        if (
            matched.startswith("***")
            or matched.startswith("**_")
            or matched.startswith("_**")
        ):
            # 加粗斜体
            inner = match.group(2) or match.group(3) or match.group(4) or ""
            page.evaluate("document.execCommand('bold', false, null)")
            page.evaluate("document.execCommand('italic', false, null)")
            page.type_text(inner, delay_ms=20)
            page.evaluate("document.execCommand('italic', false, null)")
            page.evaluate("document.execCommand('bold', false, null)")
        elif matched.startswith("**"):
            # 加粗
            inner = match.group(5) or ""
            page.evaluate("document.execCommand('bold', false, null)")
            page.type_text(inner, delay_ms=20)
            page.evaluate("document.execCommand('bold', false, null)")
        elif matched.startswith("_"):
            # 斜体
            inner = match.group(6) or ""
            page.evaluate("document.execCommand('italic', false, null)")
            page.type_text(inner, delay_ms=20)
            page.evaluate("document.execCommand('italic', false, null)")

        last_end = match.end()

    # 输出剩余普通文本
    if last_end < len(text):
        page.type_text(text[last_end:], delay_ms=20)


def _upload_cover(page: Page, cover_path: str) -> None:
    """上传文章封面图片。

    文章封面没有常驻的 input[type=file]，而是通过 JS 动态创建临时 input 并唤起
    系统文件选择框。使用 CDP 的 Page.setInterceptFileChooserDialog 拦截文件选择，
    拦截后页面 JS 已创建临时 input[type=file]，再通过 DOM.setFileInputFiles 注入文件。

    流程：
    1. 开启文件选择拦截
    2. 点击「点击上传封面图」按钮（类名前缀为 mycard-info）
    3. 轮询等待 Page.fileChooserOpened 事件（拦截后系统对话框不会弹出）
    4. 拦截成功后，页面已有临时 input[type=file]，用 DOM.setFileInputFiles 注入文件
    5. 关闭拦截
    """
    if not cover_path:
        return

    logger.info("上传文章封面: %s", cover_path)
    abs_path = str(Path(cover_path).resolve())

    # 步骤1：开启文件选择拦截
    intercept_enabled = False
    try:
        page._send_session("Page.setInterceptFileChooserDialog", {"enabled": True})
        intercept_enabled = True
        logger.info("已开启文件选择拦截")
    except Exception as enable_error:
        logger.warning("开启文件选择拦截失败: %s", enable_error)

    # 步骤2：点击「点击上传封面图」按钮（类名前缀为 mycard-info）
    btn_clicked = page.evaluate(
        """
        (() => {
            const els = document.querySelectorAll('[class*="mycard-info"]');
            for (const el of els) {
                if (el.textContent.trim().includes('点击上传封面图')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not btn_clicked:
        logger.warning("未找到「点击上传封面图」按钮，跳过封面上传")
        if intercept_enabled:
            _disable_file_chooser_intercept(page)
        return

    if intercept_enabled:
        # 步骤3：轮询等待 Page.fileChooserOpened 事件
        import json as _json

        file_chooser_received = False
        backend_node_id = None
        deadline = time.monotonic() + 5.0

        while time.monotonic() < deadline:
            # 先检查缓存的事件
            for idx, event in enumerate(page._pending_events):
                if event.get("method") == "Page.fileChooserOpened":
                    params = event.get("params", {})
                    backend_node_id = params.get("backendNodeId")
                    page._pending_events.pop(idx)
                    file_chooser_received = True
                    break
            if file_chooser_received:
                break

            try:
                raw = page._ws.recv(timeout=0.5)
            except Exception:
                continue
            if not raw:
                continue
            try:
                data = _json.loads(raw)
            except _json.JSONDecodeError:
                continue

            if data.get("method") == "Page.fileChooserOpened":
                params = data.get("params", {})
                backend_node_id = params.get("backendNodeId")
                file_chooser_received = True
                break
            elif "method" in data:
                page._pending_events.append(data)

        if file_chooser_received:
            logger.info(
                "收到 fileChooserOpened 事件 (backendNodeId=%s)", backend_node_id
            )

            # 步骤4：通过 DOM.setFileInputFiles 注入文件
            upload_success = False

            # 策略A：使用 backendNodeId 直接注入
            if backend_node_id:
                try:
                    page._send_session(
                        "DOM.setFileInputFiles",
                        {"backendNodeId": backend_node_id, "files": [abs_path]},
                    )
                    upload_success = True
                    logger.info("封面上传完成（backendNodeId 注入）")
                except Exception as node_error:
                    logger.info("backendNodeId 注入失败: %s，尝试 selector", node_error)

            # 策略B：通过 selector 查找临时 input[type=file] 注入
            if not upload_success:
                try:
                    page.set_file_input("input[type='file']", [abs_path])
                    upload_success = True
                    logger.info("封面上传完成（selector 注入）")
                except Exception as sel_error:
                    logger.warning("selector 注入也失败: %s", sel_error)

            if upload_success:
                sleep_random(3500, 4500)
                _close_cover_edit_modal(page)
        else:
            logger.warning("未收到 fileChooserOpened 事件")

        _disable_file_chooser_intercept(page)
    else:
        # 拦截未开启，等待系统对话框弹出后尝试直接注入临时 input
        sleep_random(2500, 2800)
        try:
            page.set_file_input("input[type='file']", [abs_path])
            sleep_random(3500, 4500)
            logger.info("封面上传完成（无拦截直接注入）")
            _close_cover_edit_modal(page)
        except Exception:
            logger.warning("封面上传跳过（无拦截且无 file input）")


def _close_cover_edit_modal(page: Page) -> None:
    """关闭封面编辑弹窗。

    上传封面后会自动弹出编辑封面弹窗，需要等待约 3 秒后点击「完成」按钮关闭。
    查找方法：先找类名前缀为 modalContainer 的弹窗容器，
    再在容器内找 class 为 semi-button 且 text 为「完成」的 button。
    """
    sleep_random(5000, 5500)  # 等待编辑封面弹窗弹出

    done_clicked = page.evaluate(
        """
        (() => {
            const modal = document.querySelector('[class*="modalContainer"]');
            if (!modal) return 'no-modal';
            const btns = modal.querySelectorAll('button.semi-button');
            for (const btn of btns) {
                if (btn.textContent.trim() === '完成') {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        btn.click();
                        return 'clicked';
                    }
                }
            }
            return 'no-done-btn';
        })()
        """
    )

    if done_clicked == "clicked":
        sleep_random(2500, 3000)
        logger.info("已关闭封面编辑弹窗")
    else:
        logger.warning("封面编辑弹窗关闭失败（状态: %s）", done_clicked)
    sleep_random(5500, 7000)


def _disable_file_chooser_intercept(page: Page) -> None:
    """关闭文件选择拦截。"""
    try:
        page._send_session("Page.setInterceptFileChooserDialog", {"enabled": False})
    except Exception:
        pass


def _add_topics(page: Page, topics: list[str]) -> None:
    """添加话题（点击「添加话题」→ 搜索弹窗中逐个搜索并选择）。

    「添加话题」按钮：类名前缀为 tagText 且 text 含「添加话题」的元素。
    """
    logger.info("添加话题: %s", topics)

    # 点击「添加话题」按钮（类名前缀为 tagText 且 text 含「添加话题」）
    topic_btn_result = page.evaluate(
        """
        (() => {
            const els = document.querySelectorAll('[class*="tagText"]');
            for (const el of els) {
                if (el.textContent.trim().includes('添加话题')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not topic_btn_result:
        logger.warning("未找到「添加话题」按钮，跳过话题添加")
        return

    sleep_random(3000, 3500)  # 等待搜索弹窗打开

    # 逐个添加话题
    for topic in topics:
        _add_single_topic(page, topic)

    # 点击「确认添加」按钮（class 为 semi-button 的 button 且子元素 text 含「确认添加」）
    confirm_clicked = page.evaluate(
        """
        (() => {
            const btns = document.querySelectorAll('button.semi-button');
            for (const btn of btns) {
                if (btn.textContent.trim().includes('确认添加')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if confirm_clicked:
        sleep_random(2500, 2800)
        logger.info("话题添加完成")
    else:
        logger.warning("未找到「确认添加」按钮，话题可能未完整提交")


def _add_single_topic(page: Page, topic: str) -> None:
    """在话题搜索弹窗中搜索并选择单个话题。

    搜索框：class 为 semi-input 的 input 且 placeholder 含「添加的话题」。
    搜索结果容器：类名前缀为 searchDropdown 的元素。
    第一项：容器内第一个类名前缀为 dropdownItem 的元素。
    """
    logger.info("添加话题: %s", topic)

    # 找到话题搜索输入框（class 为 semi-input 且 placeholder 含「添加的话题」）
    search_input_result = page.evaluate(
        """
        (() => {
            const inputs = document.querySelectorAll('input.semi-input');
            for (const inp of inputs) {
                const ph = inp.getAttribute('placeholder') || '';
                if (ph.includes('添加的话题')) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        inp.value = '';
                        inp.click();
                        inp.focus();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not search_input_result:
        logger.warning("未找到话题搜索输入框，跳过话题: %s", topic)
        return

    sleep_random(2300, 2500)
    page.type_text(topic, delay_ms=80)
    sleep_random(3500, 4000)  # 等待搜索联想结果加载

    # 在类名前缀为 searchDropdown 的容器内点击第一个类名前缀为 dropdownItem 的元素
    topic_selected = page.evaluate(
        """
        (() => {
            const dropdown = document.querySelector('[class*="searchDropdown"]');
            if (!dropdown) return 'no-dropdown';
            const firstItem = dropdown.querySelector('[class*="dropdownItem"]');
            if (!firstItem) return 'no-item';
            const rect = firstItem.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                firstItem.click();
                return firstItem.textContent.trim().slice(0, 20);
            }
            return 'not-visible';
        })()
        """
    )

    if topic_selected and topic_selected not in (
        "no-dropdown",
        "no-item",
        "not-visible",
    ):
        sleep_random(2300, 2500)
        logger.info("已选择话题: %s", topic_selected)
    else:
        logger.warning("未找到话题搜索结果（状态: %s）: %s", topic_selected, topic)


def _select_music(page: Page, music_name: str) -> None:
    """选择背景音乐。

    与图文发布保持一致的实现：
    1. 找所有 class 含 action 的元素，点击 text 为「选择音乐」的那个
    2. 找 input.semi-input 且 placeholder 含「音乐」的搜索框，输入音乐名
    3. 等待 3 秒后，在 class 含 music-collection-container 的容器里
       找第一个 class 含 card-container 的卡片，点击其 button.semi-button 中 text 为「使用」的按钮
    """
    logger.info("选择背景音乐: %s", music_name)

    # 步骤1：找 class 含 action 的元素，点击 text 为「选择音乐」的那个
    music_btn_result = page.evaluate(
        """
        (() => {
            const allEls = document.querySelectorAll('[class*="action"]');
            for (const el of allEls) {
                const text = el.textContent.trim();
                if (text === '选择音乐' || text === '修改音乐' || text === '添加音乐') {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        el.click();
                        return text;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not music_btn_result:
        logger.warning("未找到「选择音乐」按钮，跳过音乐选择")
        return

    logger.info("已点击音乐触发区域（%s），等待 drawer 打开", music_btn_result)
    sleep_random(3500, 4000)

    # 步骤2：找 input.semi-input 且 placeholder 含「音乐」的搜索框并输入
    search_clicked = page.evaluate(
        """
        (() => {
            const inputs = document.querySelectorAll('input.semi-input');
            for (const inp of inputs) {
                const ph = inp.getAttribute('placeholder') || '';
                if (ph.includes('音乐')) {
                    const rect = inp.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        inp.click();
                        inp.focus();
                        return true;
                    }
                }
            }
            return false;
        })()
        """
    )

    if not search_clicked:
        logger.warning("未找到音乐搜索输入框，跳过")
        page.press_key("Escape")
        return

    logger.info("已聚焦音乐搜索框，开始输入")
    page.type_text(music_name, delay_ms=80)
    sleep_random(5000, 5500)  # 等待搜索结果加载

    # 步骤3：在 music-collection-container 容器里找第一个 card-container 卡片，点击「使用」按钮
    music_applied = page.evaluate(
        """
        (() => {
            const container = document.querySelector('[class*="music-collection-container"]');
            if (!container) return 'no-container';

            const firstCard = container.querySelector('[class*="card-container"]');
            if (!firstCard) return 'no-card';

            firstCard.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
            firstCard.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));

            const btns = firstCard.querySelectorAll('button.semi-button');
            for (const btn of btns) {
                if (btn.textContent.trim() === '使用') {
                    btn.click();
                    return 'clicked';
                }
            }
            return 'no-use-btn';
        })()
        """
    )

    if music_applied != "clicked":
        logger.warning(
            "音乐「使用」按钮点击失败（状态: %s），关闭 drawer", music_applied
        )
        page.press_key("Escape")
        return

    sleep_random(2500, 2800)
    logger.info("已选择音乐: %s", music_name)


def _set_visibility(page: Page, visibility: str) -> None:
    """设置可见范围（radio 单选）。

    实际 HTML 结构与图文发布一致：
    <label class="radio-d4zkru">
        <input type="checkbox" class="radio-native-p6VBGt" value="...">
        <span>公开</span>
    </label>
    通过找包含目标文案的 label[class*="radio"] 并点击。
    """
    if visibility == "公开":
        return

    logger.info("设置可见范围: %s", visibility)

    clicked = page.evaluate(
        f"""
        (() => {{
            // 策略1：找 class 含 radio 的 label，通过 span 文案匹配
            const labels = document.querySelectorAll('label[class*="radio"]');
            for (const label of labels) {{
                if (label.textContent.trim().includes({json.dumps(visibility)})) {{
                    const rect = label.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {{
                        label.click();
                        return 'by-label';
                    }}
                }}
            }}
            // 策略2：找 class 含 radio 的 checkbox input，通过父元素文案匹配
            const checkboxes = document.querySelectorAll('input[type="checkbox"][class*="radio"]');
            for (const cb of checkboxes) {{
                const label = cb.closest('label') || cb.parentElement;
                if (label && label.textContent.trim().includes({json.dumps(visibility)})) {{
                    label.click();
                    return 'by-checkbox';
                }}
            }}
            return false;
        }})()
        """
    )

    if clicked:
        logger.info("已设置可见范围: %s（策略: %s）", visibility, clicked)
    else:
        logger.warning("未找到可见范围选项「%s」，跳过", visibility)
    sleep_random(2200, 2400)
