"""点赞、收藏。"""

from __future__ import annotations

import json
import logging

from .cdp import Page
from .human import sleep_random
from .selectors import (
    NOTE_COLLECT_BUTTON,
    NOTE_COLLECT_COLLECTED,
    NOTE_COLLECT_NOT_COLLECTED,
    NOTE_LIKE_BUTTON,
    NOTE_LIKE_DIGGED,
    NOTE_LIKE_NOT_DIGGED,
    favorite_button_xpath,
    like_button_xpath,
)
from .urls import make_note_detail_url, make_video_detail_url

logger = logging.getLogger(__name__)


def _check_like_state(page: Page, video_id: str) -> bool | None:
    """检查点赞状态。返回 True 已点赞，False 未点赞，None 元素未找到。

    通过类名数量判断：已点赞 2 个 class，未点赞 1 个 class。
    """
    xpath = like_button_xpath(video_id)
    return page.evaluate(
        f"""
        (() => {{
            const result = document.evaluate(
                {json.dumps(xpath)}, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null
            );
            const btn = result.singleNodeValue;
            if (!btn) return null;
            return btn.classList.length >= 2;
        }})()
        """
    )


def like_video(page: Page, video_id: str, unlike: bool = False) -> dict:
    """点赞/取消点赞视频。

    通过 detail-video-info 容器内 video-share-icon-container 的兄弟关系定位点赞按钮。

    Args:
        page: CDP Page 对象
        video_id: 视频 ID
        unlike: True 表示取消点赞

    Returns:
        dict: {"success": bool, "message": str, "liked": bool}
    """
    video_url = make_video_detail_url(video_id)
    page.navigate(video_url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(1000, 2000)

    try:
        liked = _check_like_state(page, video_id)
        if liked is None:
            return {
                "success": False,
                "message": "点赞按钮未找到",
                "video_id": video_id,
            }

        if unlike and not liked:
            return {
                "success": True,
                "message": "视频未点赞，无需取消",
                "liked": False,
                "video_id": video_id,
            }

        if not unlike and liked:
            return {
                "success": True,
                "message": "视频已点赞",
                "liked": True,
                "video_id": video_id,
            }

        if not page.click_element_xpath(like_button_xpath(video_id)):
            return {
                "success": False,
                "message": "点赞按钮点击失败",
                "video_id": video_id,
            }

        sleep_random(1500, 2500)  # 等待状态更新
        new_liked = _check_like_state(page, video_id)

        action = "取消点赞" if unlike else "点赞"
        return {
            "success": True,
            "message": f"{action}成功",
            "liked": new_liked if new_liked is not None else liked,
            "video_id": video_id,
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {e}",
            "video_id": video_id,
        }


# ========== 笔记（图文）点赞/收藏 API ==========


def _navigate_to_note(page: Page, note_id: str) -> None:
    """导航到笔记详情页并等待稳定。"""
    url = make_note_detail_url(note_id)
    page.navigate(url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(2000, 3000)


def _check_note_like_state(page: Page) -> bool | None:
    """检查笔记点赞状态。返回 True 已点赞，False 未点赞，None 元素未找到。

    通过 data-e2e-state 属性判断状态。
    """
    if page.has_element(NOTE_LIKE_DIGGED):
        return True
    if page.has_element(NOTE_LIKE_NOT_DIGGED):
        return False
    return None


def _check_note_collect_state(page: Page) -> bool | None:
    """检查笔记收藏状态。返回 True 已收藏，False 未收藏，None 元素未找到。

    通过 data-e2e-state 属性判断状态。
    """
    if page.has_element(NOTE_COLLECT_COLLECTED):
        return True
    if page.has_element(NOTE_COLLECT_NOT_COLLECTED):
        return False
    return None


def like_note(page: Page, note_id: str, unlike: bool = False) -> dict:
    """点赞/取消点赞笔记（图文）。

    通过 data-e2e-state 属性判断当前状态。

    Args:
        page: CDP Page 对象
        note_id: 笔记 ID
        unlike: True 表示取消点赞

    Returns:
        dict: {"success": bool, "message": str, "liked": bool, "note_id": str}
    """
    _navigate_to_note(page, note_id)

    try:
        liked = _check_note_like_state(page)
        if liked is None:
            return {
                "success": False,
                "message": "点赞按钮未找到",
                "note_id": note_id,
            }

        if unlike and not liked:
            return {
                "success": True,
                "message": "笔记未点赞，无需取消",
                "liked": False,
                "note_id": note_id,
            }

        if not unlike and liked:
            return {
                "success": True,
                "message": "笔记已点赞",
                "liked": True,
                "note_id": note_id,
            }

        if not page.click_element(NOTE_LIKE_BUTTON):
            return {
                "success": False,
                "message": "点赞按钮点击失败",
                "note_id": note_id,
            }

        sleep_random(1500, 2500)  # 等待状态更新
        new_liked = _check_note_like_state(page)

        action = "取消点赞" if unlike else "点赞"
        return {
            "success": True,
            "message": f"{action}成功",
            "liked": new_liked if new_liked is not None else (not liked),
            "note_id": note_id,
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {e}",
            "note_id": note_id,
        }


def collect_note(page: Page, note_id: str, uncollect: bool = False) -> dict:
    """收藏/取消收藏笔记（图文）。

    通过 data-e2e-state 属性判断当前状态。

    Args:
        page: CDP Page 对象
        note_id: 笔记 ID
        uncollect: True 表示取消收藏

    Returns:
        dict: {"success": bool, "message": str, "collected": bool, "note_id": str}
    """
    _navigate_to_note(page, note_id)

    try:
        collected = _check_note_collect_state(page)
        if collected is None:
            return {
                "success": False,
                "message": "收藏按钮未找到",
                "note_id": note_id,
            }

        if uncollect and not collected:
            return {
                "success": True,
                "message": "笔记未收藏，无需取消",
                "collected": False,
                "note_id": note_id,
            }

        if not uncollect and collected:
            return {
                "success": True,
                "message": "笔记已收藏",
                "collected": True,
                "note_id": note_id,
            }

        if not page.click_element(NOTE_COLLECT_BUTTON):
            return {
                "success": False,
                "message": "收藏按钮点击失败",
                "note_id": note_id,
            }

        sleep_random(1500, 2500)  # 等待状态更新
        new_collected = _check_note_collect_state(page)

        action = "取消收藏" if uncollect else "收藏"
        return {
            "success": True,
            "message": f"{action}成功",
            "collected": new_collected if new_collected is not None else (not collected),
            "note_id": note_id,
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {e}",
            "note_id": note_id,
        }


def _check_favorite_state(page: Page, video_id: str) -> bool | None:
    """检查收藏状态。返回 True 已收藏，False 未收藏，None 元素未找到。

    通过类名数量判断：已收藏 2 个 class，未收藏 1 个 class。
    """
    xpath = favorite_button_xpath(video_id)
    return page.evaluate(
        f"""
        (() => {{
            const result = document.evaluate(
                {json.dumps(xpath)}, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null
            );
            const btn = result.singleNodeValue;
            if (!btn) return null;
            return btn.classList.length >= 2;
        }})()
        """
    )


def favorite_video(page: Page, video_id: str, unfavorite: bool = False) -> dict:
    """收藏/取消收藏视频。

    通过 detail-video-info 容器内 video-share-icon-container 的兄弟关系定位收藏按钮。

    Args:
        page: CDP Page 对象
        video_id: 视频 ID
        unfavorite: True 表示取消收藏

    Returns:
        dict: {"success": bool, "message": str, "favorited": bool}
    """
    video_url = make_video_detail_url(video_id)
    page.navigate(video_url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(1000, 2000)

    try:
        favorited = _check_favorite_state(page, video_id)
        if favorited is None:
            return {
                "success": False,
                "message": "收藏按钮未找到",
                "video_id": video_id,
            }

        if unfavorite and not favorited:
            return {
                "success": True,
                "message": "视频未收藏，无需取消",
                "favorited": False,
                "video_id": video_id,
            }

        if not unfavorite and favorited:
            return {
                "success": True,
                "message": "视频已收藏",
                "favorited": True,
                "video_id": video_id,
            }

        if not page.click_element_xpath(favorite_button_xpath(video_id)):
            return {
                "success": False,
                "message": "收藏按钮点击失败",
                "video_id": video_id,
            }

        sleep_random(1500, 2500)  # 等待状态更新
        new_favorited = _check_favorite_state(page, video_id)

        action = "取消收藏" if unfavorite else "收藏"
        return {
            "success": True,
            "message": f"{action}成功",
            "favorited": new_favorited if new_favorited is not None else favorited,
            "video_id": video_id,
        }

    except Exception as e:
        return {
            "success": False,
            "message": f"操作失败: {e}",
            "video_id": video_id,
        }