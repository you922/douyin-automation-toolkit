"""互动操作：点赞、评论、回复、收藏。

所有操作都通过 CDP 模拟真实用户行为，包含人类行为延迟。
"""

from __future__ import annotations

import json
import logging
import random
import time

from .cdp import Page
from .errors import CaptchaDetectedError, ElementNotFoundError
from .human import (
    CAPTCHA_GENERIC_MARKERS,
    CAPTCHA_SPECIFIC_MARKERS,
    navigation_delay,
    sleep_random,
)
from .selectors import (
    COLLECT_BUTTON,
    COMMENT_INPUT_EDITABLE,
    COMMENT_SUBMIT_BUTTON,
    LIKE_BUTTON,
    PARENT_COMMENT,
    REPLY_BUTTON,
)
from .types import ActionResult
from .urls import make_video_detail_url

logger = logging.getLogger(__name__)


def _is_captcha_page(page: Page) -> bool:
    """检测是否为验证码页面。"""
    title = page.get_page_title() or ""
    if "验证" in title or "captcha" in title.lower():
        return True

    page_source = page.get_page_source()
    lower = page_source.lower()

    for marker in CAPTCHA_SPECIFIC_MARKERS:
        if marker.lower() in lower or marker in page_source:
            return True

    if len(page_source) >= 20000:
        return False

    for marker in CAPTCHA_GENERIC_MARKERS:
        if marker.lower() in lower or marker in page_source:
            return True

    return False


def _wait_past_captcha(page: Page, max_wait_seconds: int = 180) -> None:
    """若检测到验证码，等待用户手动解决。"""
    if not _is_captcha_page(page):
        return
    logger.warning("检测到验证码，请在浏览器中手动完成验证（最多等待 %d 秒）", max_wait_seconds)
    deadline = time.time() + max_wait_seconds
    while time.time() < deadline:
        if not _is_captcha_page(page):
            logger.info("验证码已解决")
            break
        time.sleep(2)
    time.sleep(2)


def _ensure_on_video_page(page: Page, video_url: str) -> None:
    """确保当前在视频详情页。"""
    import re

    current_url = page.get_current_url()
    m = re.search(r"/video/(\d+)", video_url)
    aweme_id = m.group(1) if m else ""

    if aweme_id and aweme_id in current_url:
        return

    page.navigate(video_url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(1500, 2500)
    _wait_past_captcha(page)


def like_video(page: Page, video_url: str) -> ActionResult:
    """点赞视频。

    Args:
        page: CDP 页面对象。
        video_url: 视频 URL。

    Returns:
        操作结果。
    """
    import re

    m = re.search(r"/video/(\d+)", video_url)
    video_id = m.group(1) if m else ""

    logger.info("点赞视频: %s", video_url)

    try:
        _ensure_on_video_page(page, video_url)

        # 查找点赞按钮
        page.wait_for_element(LIKE_BUTTON, timeout=10.0)
        sleep_random(300, 600)

        # 检查是否已点赞（按钮颜色/状态）
        already_liked = page.evaluate(
            f"""
            (() => {{
                const btn = document.querySelector({json.dumps(LIKE_BUTTON)});
                if (!btn) return false;
                const style = window.getComputedStyle(btn);
                const color = style.color || '';
                // 红色系表示已点赞
                return color.includes('rgb(254') || color.includes('rgb(255')
                    || btn.classList.toString().includes('active')
                    || btn.classList.toString().includes('liked');
            }})()
            """
        )

        if already_liked:
            logger.info("视频已点赞，跳过")
            return ActionResult(video_id=video_id, success=True, message="已点赞")

        # 滚动到点赞按钮可见
        page.scroll_element_into_view(LIKE_BUTTON)
        sleep_random(200, 400)

        # 点击点赞
        page.click_element(LIKE_BUTTON)
        sleep_random(500, 1000)

        # 验证点赞成功
        _wait_past_captcha(page)

        logger.info("点赞成功: %s", video_id)
        return ActionResult(video_id=video_id, success=True, message="点赞成功")

    except ElementNotFoundError:
        logger.warning("未找到点赞按钮: %s", video_url)
        return ActionResult(video_id=video_id, success=False, message="未找到点赞按钮")
    except Exception as e:
        logger.error("点赞失败: %s, 错误: %s", video_url, e)
        return ActionResult(video_id=video_id, success=False, message=str(e))


def collect_video(page: Page, video_url: str) -> ActionResult:
    """收藏视频。

    Args:
        page: CDP 页面对象。
        video_url: 视频 URL。

    Returns:
        操作结果。
    """
    import re

    m = re.search(r"/video/(\d+)", video_url)
    video_id = m.group(1) if m else ""

    logger.info("收藏视频: %s", video_url)

    try:
        _ensure_on_video_page(page, video_url)

        page.wait_for_element(COLLECT_BUTTON, timeout=10.0)
        sleep_random(300, 600)

        # 检查是否已收藏
        already_collected = page.evaluate(
            f"""
            (() => {{
                const btn = document.querySelector({json.dumps(COLLECT_BUTTON)});
                if (!btn) return false;
                return btn.classList.toString().includes('active')
                    || btn.classList.toString().includes('collected');
            }})()
            """
        )

        if already_collected:
            logger.info("视频已收藏，跳过")
            return ActionResult(video_id=video_id, success=True, message="已收藏")

        page.scroll_element_into_view(COLLECT_BUTTON)
        sleep_random(200, 400)

        page.click_element(COLLECT_BUTTON)
        sleep_random(500, 1000)

        _wait_past_captcha(page)

        logger.info("收藏成功: %s", video_id)
        return ActionResult(video_id=video_id, success=True, message="收藏成功")

    except ElementNotFoundError:
        logger.warning("未找到收藏按钮: %s", video_url)
        return ActionResult(video_id=video_id, success=False, message="未找到收藏按钮")
    except Exception as e:
        logger.error("收藏失败: %s, 错误: %s", video_url, e)
        return ActionResult(video_id=video_id, success=False, message=str(e))


def comment_video(page: Page, video_url: str, comment_text: str) -> ActionResult:
    """评论视频。

    Args:
        page: CDP 页面对象。
        video_url: 视频 URL。
        comment_text: 评论内容。

    Returns:
        操作结果。
    """
    import re

    m = re.search(r"/video/(\d+)", video_url)
    video_id = m.group(1) if m else ""

    logger.info("评论视频: %s, 内容: %s", video_url, comment_text[:30])

    try:
        _ensure_on_video_page(page, video_url)

        # 等待评论输入框出现
        page.wait_for_element(COMMENT_INPUT_EDITABLE, timeout=10.0)
        sleep_random(300, 500)

        # 点击输入框聚焦，输入评论内容
        page.click_element(COMMENT_INPUT_EDITABLE)
        sleep_random(300, 600)
        page.input_content_editable(COMMENT_INPUT_EDITABLE, comment_text)
        sleep_random(500, 1000)

        # 点击发送按钮
        if page.has_element(COMMENT_SUBMIT_BUTTON):
            page.click_element(COMMENT_SUBMIT_BUTTON)
        else:
            # 降级：按 Enter 发送
            page.press_key("Enter")

        sleep_random(1500, 2500)

        _wait_past_captcha(page)

        logger.info("评论成功: %s", video_id)
        return ActionResult(video_id=video_id, success=True, message="评论成功")

    except ElementNotFoundError as e:
        logger.warning("评论失败，未找到元素: %s", e)
        return ActionResult(video_id=video_id, success=False, message=f"未找到元素: {e}")
    except Exception as e:
        logger.error("评论失败: %s, 错误: %s", video_url, e)
        return ActionResult(video_id=video_id, success=False, message=str(e))


def reply_comment(
    page: Page,
    video_url: str,
    comment_index: int,
    reply_text: str,
) -> ActionResult:
    """回复指定评论。

    Args:
        page: CDP 页面对象。
        video_url: 视频 URL。
        comment_index: 评论索引（从 0 开始）。
        reply_text: 回复内容。

    Returns:
        操作结果。
    """
    import re

    m = re.search(r"/video/(\d+)", video_url)
    video_id = m.group(1) if m else ""

    logger.info("回复评论: %s, index=%d, 内容: %s", video_url, comment_index, reply_text[:30])

    try:
        _ensure_on_video_page(page, video_url)

        # 滚动到评论区
        page.scroll_by(0, 500)
        sleep_random(800, 1200)

        # 等待评论加载
        page.wait_for_element(PARENT_COMMENT, timeout=15.0)
        sleep_random(500, 800)

        # 获取评论数量
        comment_count = page.get_elements_count(PARENT_COMMENT)
        if comment_index >= comment_count:
            return ActionResult(
                video_id=video_id,
                success=False,
                message=f"评论索引超出范围: {comment_index} >= {comment_count}",
            )

        # 滚动到目标评论
        page.scroll_nth_element_into_view(PARENT_COMMENT, comment_index)
        sleep_random(300, 500)

        # 点击回复按钮
        reply_clicked = page.evaluate(
            f"""
            (() => {{
                const comments = document.querySelectorAll({json.dumps(PARENT_COMMENT)});
                const target = comments[{comment_index}];
                if (!target) return false;

                // 查找回复按钮
                const replyBtns = target.querySelectorAll({json.dumps(REPLY_BUTTON)});
                for (const btn of replyBtns) {{
                    if (btn.textContent.includes('回复')) {{
                        btn.click();
                        return true;
                    }}
                }}

                // 降级：点击评论文本区域触发回复
                const textArea = target.querySelector('span[class*="content"], p');
                if (textArea) {{
                    textArea.click();
                    return true;
                }}

                return false;
            }})()
            """
        )

        if not reply_clicked:
            return ActionResult(
                video_id=video_id,
                success=False,
                message="未找到回复按钮",
            )

        sleep_random(500, 800)

        # 等待回复输入框出现
        page.wait_for_element(COMMENT_INPUT_EDITABLE, timeout=10.0)
        sleep_random(300, 500)

        # 点击输入框聚焦，输入回复内容
        page.click_element(COMMENT_INPUT_EDITABLE)
        sleep_random(300, 600)
        page.input_content_editable(COMMENT_INPUT_EDITABLE, reply_text)
        sleep_random(500, 1000)

        # 发送回复
        if page.has_element(COMMENT_SUBMIT_BUTTON):
            page.click_element(COMMENT_SUBMIT_BUTTON)
        else:
            page.press_key("Enter")

        sleep_random(1500, 2500)

        _wait_past_captcha(page)

        logger.info("回复成功: %s, index=%d", video_id, comment_index)
        return ActionResult(video_id=video_id, success=True, message="回复成功")

    except ElementNotFoundError as e:
        logger.warning("回复失败，未找到元素: %s", e)
        return ActionResult(video_id=video_id, success=False, message=f"未找到元素: {e}")
    except Exception as e:
        logger.error("回复失败: %s, 错误: %s", video_url, e)
        return ActionResult(video_id=video_id, success=False, message=str(e))


def batch_interact(
    page: Page,
    video_urls: list[str],
    actions: list[str],
    comment_text: str = "",
    delay_between: tuple[int, int] = (3000, 8000),
) -> list[ActionResult]:
    """批量互动操作。

    Args:
        page: CDP 页面对象。
        video_urls: 视频 URL 列表。
        actions: 操作列表（like/collect/comment）。
        comment_text: 评论内容（仅 comment 操作需要）。
        delay_between: 操作间隔（毫秒）。

    Returns:
        操作结果列表。
    """
    results: list[ActionResult] = []

    for i, url in enumerate(video_urls):
        logger.info("批量互动 [%d/%d]: %s", i + 1, len(video_urls), url)

        for action in actions:
            if action == "like":
                result = like_video(page, url)
            elif action == "collect":
                result = collect_video(page, url)
            elif action == "comment" and comment_text:
                result = comment_video(page, url, comment_text)
            else:
                result = ActionResult(
                    video_id="",
                    success=False,
                    message=f"未知操作: {action}",
                )
            results.append(result)

            # 操作间隔
            if action != actions[-1]:
                sleep_random(1000, 2000)

        # 视频间隔
        if i < len(video_urls) - 1:
            sleep_random(*delay_between)

    success_count = sum(1 for r in results if r.success)
    logger.info("批量互动完成: %d/%d 成功", success_count, len(results))
    return results
