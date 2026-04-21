"""评论、回复。"""

from __future__ import annotations

import logging

from .cdp import Page
from .human import sleep_random
from .selectors import (
    COMMENT_INPUT_EDITABLE,
    COMMENT_LOADING_XPATH,
    COMMENT_NO_MORE_XPATH,
    COMMENT_SCROLL_CONTAINER,
    NOTE_COMMENT_BUTTON,
    NOTE_COMMENT_CONTAINER,
    NOTE_COMMENT_INPUT,
    NOTE_COMMENT_LOADING_XPATH,
    NOTE_COMMENT_NO_MORE_XPATH,
    NOTE_COMMENT_SCROLL_CONTAINER,
    NOTE_COMMENT_TAB_XPATH,
    SECOND_VERIFY_CONTAINER,
    note_comment_tooltip_selector,
    note_reply_button_xpath,
    reply_button_xpath,
    reply_input_selector,
)
from .urls import make_note_detail_url, make_video_detail_url

logger = logging.getLogger(__name__)


# ========== 原子方法 ==========


def _navigate_to_video(page: Page, video_id: str) -> None:
    """导航到视频详情页并等待稳定。"""
    url = make_video_detail_url(video_id)
    page.navigate(url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(5000, 10000)  # 视频加载需要更多时间


def _navigate_to_note(page: Page, note_id: str) -> None:
    """导航到笔记（图文）详情页并等待稳定。"""
    url = make_note_detail_url(note_id)
    page.navigate(url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(3000, 5000)


def _click_note_comment_tab(page: Page) -> bool:
    """点击笔记评论按钮切换到评论区。

    优先使用 data-e2e="feed-comment-icon" 选择器，回退到 XPath 文本匹配。

    Returns:
        True 点击成功，False 未找到按钮。
    """
    # 优先使用 data-e2e 属性选择器（更稳定）
    if page.has_element(NOTE_COMMENT_BUTTON):
        page.click_element(NOTE_COMMENT_BUTTON)
        sleep_random(1000, 1500)  # 等待评论区加载
        return True
    # 回退到 XPath 文本匹配
    if page.click_element_xpath(NOTE_COMMENT_TAB_XPATH):
        sleep_random(1000, 1500)
        return True
    return False


def _get_note_comment_input_selector(page: Page) -> str | None:
    """获取笔记评论输入框选择器。"""
    if page.has_element(NOTE_COMMENT_INPUT):
        return NOTE_COMMENT_INPUT
    return None


def _get_comment_input_selector(page: Page) -> str | None:
    """获取主评论输入框选择器。"""
    if page.has_element(COMMENT_INPUT_EDITABLE):
        return COMMENT_INPUT_EDITABLE
    return None


def _input_comment_and_send(page: Page, content: str, selector: str | None = None) -> dict:
    """点击输入框聚焦，输入内容，按 Enter 发送。

    Args:
        page: CDP Page 对象。
        content: 评论/回复内容。
        selector: 输入框选择器（contenteditable 或容器），None 时使用主评论输入框。

    Returns:
        dict: {"success": bool, "need_verify": bool, "message": str}
    """
    sel = selector or _get_comment_input_selector(page)
    if not sel:
        return {"success": False, "need_verify": False, "message": "输入框未找到"}

    page.scroll_element_into_view(sel)
    sleep_random(300, 600)
    page.click_element(sel)
    sleep_random(300, 600)

    page.input_content_editable(sel, content)
    sleep_random(200, 400)

    page.press_key("Enter")
    sleep_random(5000, 5500)  # 等待 5 秒，检测是否触发二次验证

    # 检测是否触发二次验证
    if page.has_element(SECOND_VERIFY_CONTAINER):
        logger.warning("检测到身份验证弹窗，请在浏览器中完成验证")
        return {
            "success": False,
            "need_verify": True,
            "message": "触发二次验证，请在浏览器中完成身份验证后重试",
        }

    return {"success": True, "need_verify": False, "message": "发送成功"}


# ========== 公开 API ==========


def post_comment(page: Page, video_id: str, content: str) -> dict:
    """发表评论。

    Args:
        page: CDP Page 对象。
        video_id: 视频 ID。
        content: 评论内容。

    Returns:
        dict: {"success": bool, "message": str, "video_id": str, "content": str}
    """
    _navigate_to_video(page, video_id)

    result = _input_comment_and_send(page, content)
    if not result["success"]:
        return {
            "success": False,
            "message": result["message"],
            "need_verify": result.get("need_verify", False),
            "video_id": video_id,
            "content": content,
        }

    return {
        "success": True,
        "message": "评论发送成功",
        "video_id": video_id,
        "content": content,
    }


def reply_comment(
    page: Page,
    video_id: str,
    comment_id: str,
    content: str,
) -> dict:
    """回复评论。

    流程：定位 comment-item（含 tooltip_{comment_id}）→ 点击「回复」→ 等待输入框出现 → 输入并发送。

    Args:
        page: CDP Page 对象。
        video_id: 视频 ID。
        comment_id: 评论 ID（对应 tooltip_{comment_id}），用于定位该条评论。
        content: 回复内容。

    Returns:
        dict: {"success": bool, "message": str, "video_id": str, "comment_id": str, "content": str}
    """
    _navigate_to_video(page, video_id)

    # 1. 定位评论；若未找到则尝试加载更多
    tooltip_selector = f"#tooltip_{comment_id}"
    max_load_attempts = 15  # 最多尝试加载 15 次
    for attempt in range(max_load_attempts):
        if page.has_element(tooltip_selector):
            break
        # 评论未找到，可能是未加载：在滚动容器内缓慢滚动以触发加载
        if page.scroll_element_into_view_xpath_slow(
            COMMENT_LOADING_XPATH, container_selector=COMMENT_SCROLL_CONTAINER
        ):
            sleep_random(2500, 4000)  # 等待加载（降低速度，避免过快）
        else:
            # 无加载中元素，检查是否已到底
            if page.has_element_xpath(COMMENT_NO_MORE_XPATH):
                return {
                    "success": False,
                    "message": f"未找到评论（comment_id={comment_id}），可能已被删除",
                    "video_id": video_id,
                    "comment_id": comment_id,
                    "content": content,
                }
            sleep_random(3000, 8000)
    else:
        return {
            "success": False,
            "message": f"未找到评论（comment_id={comment_id}），可能已被删除",
            "video_id": video_id,
            "comment_id": comment_id,
            "content": content,
        }

    page.scroll_element_into_view_slow(tooltip_selector)
    sleep_random(800, 1500)

    # 2. 点击「回复」按钮（comment-item-stats-container 内 span「回复」）
    if not page.click_element_xpath(reply_button_xpath(comment_id)):
        return {
            "success": False,
            "message": "回复按钮未找到或点击失败",
            "video_id": video_id,
            "comment_id": comment_id,
            "content": content,
        }

    # 3. 等待回复输入框出现（comment-item 内 comment-input-inner-container）
    reply_sel = reply_input_selector(comment_id)
    for _ in range(20):  # 最多等待约 5 秒
        if page.has_element(reply_sel):
            break
        sleep_random(250, 400)
    else:
        return {
            "success": False,
            "message": "回复输入框未出现",
            "video_id": video_id,
            "comment_id": comment_id,
            "content": content,
        }

    sleep_random(300, 600)

    # 4. 在评论 item 内的回复输入框中输入并发送
    result = _input_comment_and_send(page, content, selector=reply_sel)
    if not result["success"]:
        return {
            "success": False,
            "message": result["message"],
            "need_verify": result.get("need_verify", False),
            "video_id": video_id,
            "comment_id": comment_id,
            "content": content,
        }

    return {
        "success": True,
        "message": "回复发送成功",
        "video_id": video_id,
        "comment_id": comment_id,
        "content": content,
    }


# ========== 笔记（图文）评论 API ==========


def post_note_comment(page: Page, note_id: str, content: str) -> dict:
    """对笔记（图文）发表评论。

    流程：导航到笔记页 → 点击评论 Tab → 定位输入框 → 输入并发送。

    Args:
        page: CDP Page 对象。
        note_id: 笔记 ID。
        content: 评论内容。

    Returns:
        dict: {"success": bool, "message": str, "note_id": str, "content": str}
    """
    _navigate_to_note(page, note_id)

    # 1. 点击评论 Tab 切换到评论区
    if not _click_note_comment_tab(page):
        return {
            "success": False,
            "message": "评论 Tab 未找到",
            "note_id": note_id,
            "content": content,
        }

    # 2. 等待评论容器出现
    for _ in range(20):
        if page.has_element(NOTE_COMMENT_CONTAINER):
            break
        sleep_random(250, 400)
    else:
        return {
            "success": False,
            "message": "评论区未加载",
            "note_id": note_id,
            "content": content,
        }

    # 3. 定位评论输入框并发送
    input_sel = _get_note_comment_input_selector(page)
    if not input_sel:
        return {
            "success": False,
            "message": "评论输入框未找到",
            "note_id": note_id,
            "content": content,
        }

    result = _input_comment_and_send(page, content, selector=input_sel)
    if not result["success"]:
        return {
            "success": False,
            "message": result["message"],
            "need_verify": result.get("need_verify", False),
            "note_id": note_id,
            "content": content,
        }

    return {
        "success": True,
        "message": "笔记评论发送成功",
        "note_id": note_id,
        "content": content,
    }


def reply_note_comment(
    page: Page,
    note_id: str,
    comment_id: str,
    content: str,
) -> dict:
    """回复笔记（图文）的评论。

    流程：导航到笔记页 → 点击评论 Tab → 定位评论 → 点击回复 → 输入并发送。
    笔记的评论和回复使用同一个输入框。

    Args:
        page: CDP Page 对象。
        note_id: 笔记 ID。
        comment_id: 评论 ID（对应 tooltip_{comment_id}）。
        content: 回复内容。

    Returns:
        dict: {"success": bool, "message": str, "note_id": str, "comment_id": str, "content": str}
    """
    _navigate_to_note(page, note_id)

    # 1. 点击评论 Tab 切换到评论区
    if not _click_note_comment_tab(page):
        return {
            "success": False,
            "message": "评论 Tab 未找到",
            "note_id": note_id,
            "comment_id": comment_id,
            "content": content,
        }

    # 2. 等待评论容器出现
    for _ in range(20):
        if page.has_element(NOTE_COMMENT_CONTAINER):
            break
        sleep_random(250, 400)
    else:
        return {
            "success": False,
            "message": "评论区未加载",
            "note_id": note_id,
            "comment_id": comment_id,
            "content": content,
        }

    # 3. 定位目标评论；若未找到则尝试加载更多
    tooltip_selector = note_comment_tooltip_selector(comment_id)
    max_load_attempts = 15
    for attempt in range(max_load_attempts):
        if page.has_element(tooltip_selector):
            break
        # 评论未找到，尝试滚动加载更多
        if page.scroll_element_into_view_xpath_slow(
            NOTE_COMMENT_LOADING_XPATH, container_selector=NOTE_COMMENT_SCROLL_CONTAINER
        ):
            sleep_random(2500, 4000)
        else:
            # 无加载中元素，检查是否已到底
            if page.has_element_xpath(NOTE_COMMENT_NO_MORE_XPATH):
                return {
                    "success": False,
                    "message": f"未找到评论（comment_id={comment_id}），可能已被删除",
                    "note_id": note_id,
                    "comment_id": comment_id,
                    "content": content,
                }
            sleep_random(3000, 8000)
    else:
        return {
            "success": False,
            "message": f"未找到评论（comment_id={comment_id}），可能已被删除",
            "note_id": note_id,
            "comment_id": comment_id,
            "content": content,
        }

    page.scroll_element_into_view_slow(tooltip_selector)
    sleep_random(800, 1500)

    # 4. 点击「回复」按钮
    if not page.click_element_xpath(note_reply_button_xpath(comment_id)):
        return {
            "success": False,
            "message": "回复按钮未找到或点击失败",
            "note_id": note_id,
            "comment_id": comment_id,
            "content": content,
        }

    sleep_random(500, 800)

    # 5. 笔记的评论和回复使用同一个输入框，直接使用该输入框
    input_sel = _get_note_comment_input_selector(page)
    if not input_sel:
        return {
            "success": False,
            "message": "回复输入框未找到",
            "note_id": note_id,
            "comment_id": comment_id,
            "content": content,
        }

    result = _input_comment_and_send(page, content, selector=input_sel)
    if not result["success"]:
        return {
            "success": False,
            "message": result["message"],
            "need_verify": result.get("need_verify", False),
            "note_id": note_id,
            "comment_id": comment_id,
            "content": content,
        }

    return {
        "success": True,
        "message": "笔记回复发送成功",
        "note_id": note_id,
        "comment_id": comment_id,
        "content": content,
    }
