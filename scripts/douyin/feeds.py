"""首页 Feed 列表。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .errors import NoVideosError
from .types import Feed
from .urls import HOME_URL

logger = logging.getLogger(__name__)

# 从页面提取视频数据的 JS
_EXTRACT_FEEDS_JS = """
(() => {
    // 尝试从 __RENDER_DATA__ 提取
    const renderDataScript = document.getElementById('RENDER_DATA');
    if (renderDataScript) {
        try {
            const renderData = JSON.parse(decodeURIComponent(renderDataScript.textContent));
            return JSON.stringify(renderData);
        } catch(e) {}
    }

    // 尝试从 window.__INITIAL_STATE__ 提取
    if (window.__INITIAL_STATE__) {
        return JSON.stringify(window.__INITIAL_STATE__);
    }

    return "";
})()
"""

def list_feeds(page: Page, count: int = 20) -> list[Feed]:
    """获取首页 Feed 列表。

    Args:
        page: CDP Page 对象
        count: 获取数量

    Returns:
        Feed 列表

    Raises:
        NoVideosError: 没有捕获到数据。
    """
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    time.sleep(2)

    # 尝试提取数据
    result = page.evaluate(_EXTRACT_FEEDS_JS)

    if not result:
        # 尝试滚动加载更多
        for _ in range(3):
            page.scroll_to_bottom()
            time.sleep(1)
            result = page.evaluate(_EXTRACT_FEEDS_JS)
            if result:
                break

    if not result:
        raise NoVideosError()

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        raise NoVideosError()

    # 解析数据结构
    feeds: list[Feed] = []

    # 尝试不同的数据路径
    video_list = []

    # 路径1: app.videoFeed
    try:
        video_list = data.get("app", {}).get("videoFeed", [])
    except Exception:
        pass

    # 路径2: data.videoList
    if not video_list:
        try:
            video_list = data.get("data", {}).get("videoList", [])
        except Exception:
            pass

    # 路径3: 直接是列表
    if not video_list and isinstance(data, list):
        video_list = data

    # 路径4: aweme_list
    if not video_list:
        try:
            video_list = data.get("aweme_list", [])
        except Exception:
            pass

    for item in video_list[:count]:
        try:
            feed = Feed.from_dict(item)
            feeds.append(feed)
        except Exception as e:
            logger.debug("解析 Feed 失败: %s", e)
            continue

    return feeds

def get_trending_feeds(page: Page, count: int = 20) -> list[Feed]:
    """获取热门视频列表。

    Args:
        page: CDP Page 对象
        count: 获取数量

    Returns:
        Feed 列表
    """
    # 热门页面的数据提取逻辑与首页类似
    return list_feeds(page, count)
