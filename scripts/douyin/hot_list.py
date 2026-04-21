"""抖音热榜获取模块。

通过 CDP 浏览器在抖音域名下执行 fetch 请求获取热榜数据。
利用浏览器已有的 cookie 和反爬机制，避免直接 HTTP 请求被风控。
"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .errors import HotListEmptyError, HotListFetchError
from .types import Feed, HotTopic
from .urls import HOME_URL, HOT_CHANNEL_HOTSPOT_API, HOT_LIST_PAGE_URL, HOT_SEARCH_LIST_API

logger = logging.getLogger(__name__)

# 最大重试次数
_MAX_RETRIES = 2

# 在浏览器上下文中执行 fetch 请求获取热榜数据
_FETCH_HOT_LIST_JS = """
(async () => {{
    try {{
        const resp = await fetch('{}', {{
            method: 'GET',
            headers: {{
                'Accept': 'application/json',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
            }},
            credentials: 'include'
        }});
        if (!resp.ok) {{
            return JSON.stringify({{ error: 'HTTP ' + resp.status, status: resp.status }});
        }}
        const data = await resp.json();
        return JSON.stringify(data);
    }} catch(e) {{
        return JSON.stringify({{ error: e.message }});
    }}
}})()
""".format(HOT_SEARCH_LIST_API)

# 从热榜页面 DOM 中提取数据（降级方案）
_EXTRACT_HOT_FROM_DOM_JS = """
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

    // 尝试从 DOM 元素中提取热榜列表
    const items = [];
    const hotItems = document.querySelectorAll(
        '[class*="hot-item"], [class*="hotItem"], [class*="trending-item"]'
    );
    hotItems.forEach((el, idx) => {
        const titleEl = el.querySelector(
            '[class*="title"], [class*="word"], [class*="text"], a, span'
        );
        const heatEl = el.querySelector(
            '[class*="heat"], [class*="hot"], [class*="count"], [class*="value"]'
        );
        const linkEl = el.querySelector('a[href]');
        if (titleEl) {
            items.push({
                word: titleEl.textContent.trim(),
                hot_value: heatEl ? parseInt(heatEl.textContent.replace(/[^0-9]/g, '')) || 0 : 0,
                url: linkEl ? linkEl.href : '',
                label: '',
                type: '未知'
            });
        }
    });

    if (items.length > 0) {
        return JSON.stringify({ data: { word_list: items } });
    }

    return '';
})()
"""


def _ensure_douyin_context(page: Page) -> None:
    """确保浏览器处于抖音域名下，以便 fetch 请求携带正确的 cookie。"""
    current_url = page.evaluate("window.location.href") or ""
    if "douyin.com" not in current_url:
        logger.info("当前不在抖音域名下，导航到抖音首页...")
        page.navigate(HOME_URL)
        page.wait_for_load()
        page.wait_dom_stable()
        time.sleep(1)


def _parse_hot_list(raw_data: dict, count: int) -> tuple[list[HotTopic], list[Feed]]:
    """解析热榜 API 返回的原始数据。

    Args:
        raw_data: API 返回的 JSON 数据。
        count: 获取数量上限。

    Returns:
        (HotTopic 列表, Feed 列表)。Feed 来自各话题的 aweme_infos。

    Raises:
        HotListEmptyError: 数据为空。
    """
    data = raw_data.get("data", {})
    word_list = data.get("word_list", [])

    if not word_list:
        raise HotListEmptyError()

    topics: list[HotTopic] = []
    all_feeds: list[Feed] = []

    for idx, item_data in enumerate(word_list[:count]):
        try:
            topic = HotTopic.from_dict(item_data, index=idx)
            topics.append(topic)

            aweme_dicts = HotTopic.parse_aweme_infos(item_data)
            for aweme_dict in aweme_dicts:
                try:
                    feed = Feed.from_dict(aweme_dict)
                    if feed.video_id:
                        all_feeds.append(feed)
                except Exception as feed_err:
                    logger.debug("解析话题 [%s] 关联视频失败: %s", topic.word[:20], feed_err)

        except Exception as e:
            logger.debug("解析热榜条目 #%d 失败: %s", idx + 1, e)
            continue

    if not topics:
        raise HotListEmptyError()

    if all_feeds:
        logger.info("从热榜话题中解析出 %d 条关联视频", len(all_feeds))

    return topics, all_feeds


def _fetch_via_api(page: Page) -> dict:
    """通过浏览器 fetch API 获取热榜数据。

    Returns:
        解析后的 JSON 字典。

    Raises:
        HotListFetchError: 请求失败。
    """
    result = page.evaluate_async(_FETCH_HOT_LIST_JS, timeout=15.0)

    if not result:
        raise HotListFetchError("fetch 返回空结果")

    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError) as e:
        raise HotListFetchError(f"JSON 解析失败: {e}") from e

    if "error" in data:
        raise HotListFetchError(f"API 错误: {data['error']}")

    return data


_FETCH_HOTSPOT_JS = """
(async () => {{
    try {{
        const resp = await fetch('{}', {{
            method: 'GET',
            headers: {{
                'Accept': 'application/json',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
            }},
            credentials: 'include'
        }});
        if (!resp.ok) {{
            return JSON.stringify({{ error: 'HTTP ' + resp.status, status: resp.status }});
        }}
        const data = await resp.json();
        return JSON.stringify(data);
    }} catch(e) {{
        return JSON.stringify({{ error: e.message }});
    }}
}})()
""".format(HOT_CHANNEL_HOTSPOT_API)


def _fetch_hotspot_videos(page: Page) -> list[Feed]:
    """通过热点频道 API 获取热榜关联视频。

    调用 /aweme/v1/web/channel/hotspot 获取热点视频列表，
    返回的 aweme_list 结构与搜索 API 的 aweme_info 完全一致。

    Args:
        page: CDP Page 对象。

    Returns:
        Feed 列表。获取失败时返回空列表（不抛异常，因为这是补充数据）。
    """
    try:
        result = page.evaluate_async(_FETCH_HOTSPOT_JS, timeout=15.0)
        if not result:
            logger.debug("hotspot API 返回空结果")
            return []

        data = json.loads(result)
        if "error" in data:
            logger.debug("hotspot API 错误: %s", data["error"])
            return []

        aweme_list = data.get("aweme_list", [])
        if not aweme_list or not isinstance(aweme_list, list):
            logger.debug("hotspot API 无 aweme_list")
            return []

        feeds: list[Feed] = []
        for aweme_data in aweme_list:
            if not isinstance(aweme_data, dict):
                continue
            try:
                feed = Feed.from_dict({"aweme_info": aweme_data})
                if feed.video_id:
                    feeds.append(feed)
            except Exception as parse_err:
                logger.debug("解析 hotspot 视频失败: %s", parse_err)

        if feeds:
            logger.info("从热点频道获取到 %d 条关联视频", len(feeds))
        return feeds

    except (json.JSONDecodeError, TypeError) as json_err:
        logger.debug("hotspot API JSON 解析失败: %s", json_err)
        return []
    except Exception as unexpected_err:
        logger.warning("hotspot API 异常: %s", unexpected_err)
        return []


def _fetch_via_dom(page: Page) -> dict:
    """通过页面 DOM 提取热榜数据（降级方案）。

    导航到热榜页面，从 SSR 数据或 DOM 元素中提取。

    Returns:
        解析后的 JSON 字典。

    Raises:
        HotListFetchError: 提取失败。
    """
    logger.info("降级方案：导航到热榜页面提取数据...")
    page.navigate(HOT_LIST_PAGE_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    time.sleep(2)

    result = page.evaluate(_EXTRACT_HOT_FROM_DOM_JS)

    if not result:
        # 尝试滚动加载
        for attempt in range(3):
            page.scroll_to_bottom()
            time.sleep(1)
            result = page.evaluate(_EXTRACT_HOT_FROM_DOM_JS)
            if result:
                break
            logger.debug("DOM 提取第 %d 次尝试无结果", attempt + 1)

    if not result:
        raise HotListFetchError("DOM 提取失败：页面中未找到热榜数据")

    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError) as e:
        raise HotListFetchError(f"DOM 数据解析失败: {e}") from e

    # 尝试从 SSR 数据中定位 word_list
    if "data" not in data or "word_list" not in data.get("data", {}):
        # 遍历嵌套结构查找 word_list
        word_list = _find_word_list(data)
        if word_list:
            data = {"data": {"word_list": word_list}}
        else:
            raise HotListFetchError("DOM 数据中未找到 word_list")

    return data


def _find_word_list(obj: dict | list, depth: int = 0) -> list | None:
    """递归查找嵌套数据中的 word_list。

    Args:
        obj: 待搜索的对象。
        depth: 当前递归深度（防止过深递归）。

    Returns:
        找到的 word_list 或 None。
    """
    if depth > 5:
        return None

    if isinstance(obj, dict):
        if "word_list" in obj and isinstance(obj["word_list"], list):
            return obj["word_list"]
        for value in obj.values():
            if isinstance(value, (dict, list)):
                result = _find_word_list(value, depth + 1)
                if result:
                    return result
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                result = _find_word_list(item, depth + 1)
                if result:
                    return result

    return None


def fetch_hot_list(page: Page, count: int = 50) -> tuple[list[HotTopic], list[Feed]]:
    """获取抖音热榜数据。

    优先通过浏览器 fetch API 获取，失败时降级到 DOM 提取。
    包含重试机制，最多重试 _MAX_RETRIES 次。

    Args:
        page: CDP Page 对象（需已连接到 Chrome）。
        count: 获取数量，默认 50。

    Returns:
        (HotTopic 列表, Feed 列表)。Feed 来自热点频道 API 和话题的 aweme_infos。

    Raises:
        HotListFetchError: 所有获取方式均失败。
        HotListEmptyError: 获取到数据但为空。
    """
    _ensure_douyin_context(page)

    last_error: Exception | None = None
    topics: list[HotTopic] = []
    feeds: list[Feed] = []

    # 方式1：通过 fetch API 获取（含重试）
    for attempt in range(_MAX_RETRIES + 1):
        try:
            if attempt > 0:
                wait_sec = 1.0 + attempt * 0.5
                logger.info("第 %d 次重试，等待 %.1f 秒...", attempt, wait_sec)
                time.sleep(wait_sec)

            raw_data = _fetch_via_api(page)
            topics, feeds = _parse_hot_list(raw_data, count)
            logger.info("通过 fetch API 获取到 %d 条热榜话题", len(topics))
            break

        except (HotListFetchError, HotListEmptyError) as e:
            last_error = e
            logger.warning("fetch API 第 %d 次尝试失败: %s", attempt + 1, e)
            continue
    else:
        # 方式2：降级到 DOM 提取
        try:
            raw_data = _fetch_via_dom(page)
            topics, feeds = _parse_hot_list(raw_data, count)
            logger.info("通过 DOM 降级获取到 %d 条热榜话题", len(topics))
        except (HotListFetchError, HotListEmptyError) as e:
            logger.error("DOM 降级也失败: %s", e)
            last_error = e
            raise HotListFetchError(
                f"所有获取方式均失败，最后错误: {last_error}"
            ) from e

    # 补充：通过热点频道 API 获取关联视频
    hotspot_feeds = _fetch_hotspot_videos(page)
    if hotspot_feeds:
        existing_ids = {f.video_id for f in feeds if f.video_id}
        for hotspot_feed in hotspot_feeds:
            if hotspot_feed.video_id and hotspot_feed.video_id not in existing_ids:
                feeds.append(hotspot_feed)
                existing_ids.add(hotspot_feed.video_id)

    return topics, feeds
