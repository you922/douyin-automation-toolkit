"""用户主页：获取用户信息、视频列表。"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .cdp import Page
from .human import (
    CAPTCHA_GENERIC_MARKERS,
    CAPTCHA_SPECIFIC_MARKERS,
    INACCESSIBLE_KEYWORDS,
    sleep_random,
)
from .search import _parse_aweme_from_response
from .selectors import USER_INFO_CONTAINER, USER_VIDEO_CARD, USER_VIDEO_LIST
from .types import Feed, UserBasicInfo, UserProfileResponse
from .urls import make_user_profile_url

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


def _is_inaccessible(page: Page) -> bool:
    """检测页面是否不可访问。"""
    page_source = page.get_page_source()
    lower = page_source.lower()
    for kw in INACCESSIBLE_KEYWORDS:
        if kw.lower() in lower:
            return True
    return False


def _is_profile_list_end(page: Page) -> bool:
    """检测用户主页视频列表是否已滚动到底部。

    抖音底部提示元素的 class 为 hash 值（如 "E5QmyeTo"），不可靠，
    因此直接匹配叶子节点的文字内容，精准且不受改版影响。
    """
    result = page.evaluate(
        """
        (() => {
            const targets = ['暂时没有更多了', '没有更多内容了', '暂无更多'];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                if (el.children.length > 0) continue;
                const text = (el.textContent || '').trim();
                if (targets.includes(text) && el.offsetParent !== null) return true;
            }
            return false;
        })()
        """
    )
    return bool(result)


def _inject_profile_fetch_interceptor(page: Page) -> None:
    """注入 fetch/XHR 拦截器，捕获用户主页的视频列表 API 响应。

    抖音用户主页滚动加载时，通过 fetch API 请求 aweme/post 接口获取更多视频。
    拦截这些响应可以获取到完整的视频数据（含 statistics、author 等）。
    """
    page.evaluate(
        """
        (() => {
            if (window.__profile_interceptor_installed__) return;
            window.__profile_interceptor_installed__ = true;
            window.__captured_profile_responses__ = [];

            const originalFetch = window.fetch;
            window.fetch = async function(...args) {
                const response = await originalFetch.apply(this, args);
                try {
                    const url = (typeof args[0] === 'string') ? args[0] : args[0]?.url || '';
                    if (url.includes('aweme') && url.includes('post')) {
                        const cloned = response.clone();
                        cloned.text().then(text => {
                            window.__captured_profile_responses__.push(text);
                        }).catch(() => {});
                    }
                } catch(e) {}
                return response;
            };

            const originalXHROpen = XMLHttpRequest.prototype.open;
            const originalXHRSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                this.__url__ = url;
                return originalXHROpen.call(this, method, url, ...rest);
            };
            XMLHttpRequest.prototype.send = function(...args) {
                this.addEventListener('load', function() {
                    try {
                        const url = this.__url__ || '';
                        if (url.includes('aweme') && url.includes('post')) {
                            window.__captured_profile_responses__.push(this.responseText);
                        }
                    } catch(e) {}
                });
                return originalXHRSend.apply(this, args);
            };
        })()
        """
    )


def _collect_profile_intercepted_responses(page: Page) -> list[dict[str, Any]]:
    """从页面全局数组中收集并清空拦截到的用户主页 API 响应。"""
    raw_responses = page.evaluate(
        """
        (() => {
            const responses = window.__captured_profile_responses__ || [];
            window.__captured_profile_responses__ = [];
            return responses;
        })()
        """
    )
    if not raw_responses or not isinstance(raw_responses, list):
        return []

    result = []
    for body_text in raw_responses:
        if body_text and isinstance(body_text, str) and len(body_text) > 10:
            result.append({"body": body_text})
    return result


def _get_profile_scroll_target(page: Page) -> tuple[float, float]:
    """获取用户主页视频列表区域的中心坐标，用于 CDP 鼠标滚轮事件。"""
    result = page.evaluate(
        """
        (() => {
            const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
            const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
            const candidates = [
                '[data-e2e="user-post-list"]',
                '#user-post-container',
                'ul[data-e2e="scroll-list"]',
                'div[class*="video-list"]',
                'div[class*="post-list"]',
                'main',
            ];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (el && el.offsetWidth > 0) {
                    const rect = el.getBoundingClientRect();
                    const centerX = rect.left + rect.width / 2;
                    const visibleTop = Math.max(rect.top, 0);
                    const visibleBottom = Math.min(rect.bottom, viewportHeight);
                    const centerY = (visibleTop + visibleBottom) / 2;
                    return {
                        x: Math.min(centerX, viewportWidth - 10),
                        y: Math.max(100, Math.min(centerY, viewportHeight - 100)),
                    };
                }
            }
            return { x: viewportWidth / 2, y: viewportHeight / 2 };
        })()
        """
    )
    return (result.get("x", 600), result.get("y", 400))


def _cdp_wheel_scroll_profile(page: Page, center_x: float, center_y: float, wheel_count: int = 3) -> None:
    """通过 CDP Input.dispatchMouseEvent 发送鼠标滚轮事件触发用户主页滚动。"""
    page._send_session("Input.dispatchMouseEvent", {
        "type": "mouseMoved",
        "x": center_x,
        "y": center_y,
    })
    time.sleep(0.2)

    for _ in range(wheel_count):
        page._send_session("Input.dispatchMouseEvent", {
            "type": "mouseWheel",
            "x": center_x,
            "y": center_y,
            "deltaX": 0,
            "deltaY": 300,
        })
        time.sleep(0.4)


def _is_feed_data_complete(feed: Feed) -> bool:
    """检查 Feed 数据是否完整（非 DOM 降级的贫乏数据）。

    DOM 提取的数据只有 title 和 video_id，其他字段全是 0/空。
    完整的 API 数据至少会有 author、like_count、create_time 等字段。
    """
    return bool(feed.author) or feed.like_count > 0 or feed.create_time > 0


def _merge_api_feeds_into_existing(
    feeds: list[Feed],
    api_feeds: list[Feed],
    existing_ids: set[str],
) -> int:
    """将 API 获取的完整数据合并到已有列表中。

    - 如果 video_id 已存在但数据不完整（DOM 降级数据），用 API 数据替换
    - 如果 video_id 不存在，追加到列表末尾

    Returns:
        新增或更新的条数。
    """
    updated = 0
    api_by_id = {af.video_id: af for af in api_feeds if af.video_id}

    for i, existing_feed in enumerate(feeds):
        if existing_feed.video_id in api_by_id and not _is_feed_data_complete(existing_feed):
            feeds[i] = api_by_id[existing_feed.video_id]
            updated += 1
            del api_by_id[existing_feed.video_id]

    for video_id, api_feed in api_by_id.items():
        if video_id not in existing_ids:
            feeds.append(api_feed)
            existing_ids.add(video_id)
            updated += 1

    return updated


def get_user_profile(
    page: Page,
    sec_uid: str,
    max_videos: int = 20,
) -> UserProfileResponse:
    """获取用户主页信息和视频列表。

    数据获取策略：
    1. 导航到用户主页
    2. 提取用户信息（SSR → DOM 降级）
    3. 注入 fetch/XHR 拦截器捕获 aweme/post API 响应
    4. 刷新页面触发首屏 API 请求（拦截器捕获完整数据）
    5. SSR RENDER_DATA 提取首屏数据
    6. CDP 鼠标滚轮事件触发无限加载
    7. 从拦截到的 API 响应中解析完整的 Feed 数据
    8. DOM 提取作为最终降级方案（仅获取 video_id 和 title）
    9. 检查数据质量，用 API 数据替换 DOM 贫乏数据

    Args:
        page: CDP 页面对象。
        sec_uid: 用户的 sec_uid；传 "self" 表示当前登录用户（我的主页）。
        max_videos: 最大视频数。

    Returns:
        UserProfileResponse 包含用户信息和视频列表。
    """
    logger.info("获取用户主页: sec_uid=%s, max_videos=%d", sec_uid, max_videos)

    profile_url = make_user_profile_url(sec_uid)
    page.navigate(profile_url)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(1500, 2500)

    _wait_past_captcha(page)

    if _is_inaccessible(page):
        logger.warning("用户主页不可访问: %s", sec_uid)
        return UserProfileResponse()

    # 提取用户信息
    user_info = _extract_user_info_from_ssr(page)
    if not user_info:
        user_info = _extract_user_info_from_dom(page)

    # 注入 fetch/XHR 拦截器（捕获后续滚动加载的 API 响应）
    _inject_profile_fetch_interceptor(page)

    # SSR 提取首屏数据（可能包含完整 statistics）
    feeds: list[Feed] = []
    existing_ids: set[str] = set()

    ssr_feeds = _extract_user_videos_from_ssr(page, max_videos)
    if ssr_feeds:
        for sf in ssr_feeds:
            if sf.video_id and sf.video_id not in existing_ids:
                feeds.append(sf)
                existing_ids.add(sf.video_id)
        logger.info("SSR 提取到 %d 条", len(feeds))

    # DOM 提取作为补充（仅获取 video_id 和 title，数据不完整）
    dom_feeds = _extract_user_videos_from_dom(page, max_videos * 2)
    for df in dom_feeds:
        if df.video_id and df.video_id not in existing_ids:
            feeds.append(df)
            existing_ids.add(df.video_id)
    if dom_feeds:
        logger.info("DOM 补充后累计 %d 条", len(feeds))

    # 滚动到页面顶部，确保从头开始加载
    page.evaluate("window.scrollTo(0, 0)")
    sleep_random(500, 1000)

    # 检查数据质量
    complete_count = sum(1 for f in feeds if _is_feed_data_complete(f))
    logger.info("首屏提取到 %d 条视频 (其中 %d 条数据完整)", len(feeds), complete_count)

    # 获取滚动目标区域中心坐标
    center_x, center_y = _get_profile_scroll_target(page)
    logger.info("滚动目标区域中心: (%.0f, %.0f)", center_x, center_y)

    # 滚动加载更多视频（同时通过 API 拦截器获取完整数据）
    max_scroll_attempts = 30
    consecutive_empty = 0
    max_consecutive_empty = 5

    for scroll_attempt in range(1, max_scroll_attempts + 1):
        # 检查是否已获取足够的完整数据
        complete_count = sum(1 for f in feeds[:max_videos] if _is_feed_data_complete(f))
        if len(feeds) >= max_videos and complete_count >= max_videos:
            logger.info("已获取 %d 条完整数据, 满足需求", complete_count)
            break

        logger.info(
            "滚动加载第 %d 次 (已获取 %d 条, 其中 %d 条完整, 目标 %d 条)",
            scroll_attempt, len(feeds), complete_count, max_videos,
        )

        # CDP 鼠标滚轮事件触发滚动
        _cdp_wheel_scroll_profile(page, center_x, center_y, wheel_count=5)
        sleep_random(2000, 3000)

        added = 0

        # 从拦截器中收集 API 响应（包含完整的 statistics、author 等数据）
        intercepted = _collect_profile_intercepted_responses(page)
        if intercepted:
            logger.info("拦截到 %d 个用户主页 API 响应", len(intercepted))

        for response_data in intercepted:
            api_feeds = _parse_aweme_from_response(response_data)
            if api_feeds:
                merged = _merge_api_feeds_into_existing(feeds, api_feeds, existing_ids)
                added += merged
                logger.info("API 响应解析出 %d 条, 合并/新增 %d 条", len(api_feeds), merged)

        # 补充：从 DOM 提取新出现的视频卡片
        dom_feeds = _extract_user_videos_from_dom(page, max_videos * 2)
        for df in dom_feeds:
            if df.video_id and df.video_id not in existing_ids:
                feeds.append(df)
                existing_ids.add(df.video_id)
                added += 1

        if added > 0:
            consecutive_empty = 0
            logger.info("本次滚动新增/更新 %d 条, 累计 %d 条", added, len(feeds))
        else:
            consecutive_empty += 1
            if _is_captcha_page(page):
                _wait_past_captcha(page)
                consecutive_empty = 0
            elif consecutive_empty >= max_consecutive_empty:
                logger.info("连续 %d 次滚动无新增, 停止加载", max_consecutive_empty)
                break

        # 每次滚动后都检测底部提示，一旦出现立即停止（不依赖 class，直接匹配文字）
        if _is_profile_list_end(page):
            logger.info("检测到列表底部提示，停止加载")
            break

    # 优先输出数据完整的 Feed（API 数据），不完整的 DOM 数据排后面
    complete_feeds = [f for f in feeds if _is_feed_data_complete(f)]
    incomplete_feeds = [f for f in feeds if not _is_feed_data_complete(f)]

    # 去重：如果完整数据中已有某个 video_id，从不完整列表中移除
    complete_ids = {f.video_id for f in complete_feeds}
    incomplete_feeds = [f for f in incomplete_feeds if f.video_id not in complete_ids]

    # 合并：完整数据优先，不完整数据补充
    merged_feeds = complete_feeds + incomplete_feeds
    final_feeds = merged_feeds[:max_videos]

    complete_count = sum(1 for f in final_feeds if _is_feed_data_complete(f))
    result = UserProfileResponse(
        user_basic_info=user_info or UserBasicInfo(),
        feeds=final_feeds,
    )
    logger.info(
        "用户主页获取完成: %s, %d 条视频 (其中 %d 条数据完整)",
        result.user_basic_info.nickname,
        len(result.feeds),
        complete_count,
    )
    return result


def get_my_profile(page: Page, max_videos: int = 20) -> UserProfileResponse:
    """获取当前登录用户自己的主页信息及视频列表。

    等价于 get_user_profile(page, "self", max_videos)。
    访问 https://www.douyin.com/user/self，登录后会自动重定向到真实主页。

    Args:
        page: CDP 页面对象。
        max_videos: 最大视频数。

    Returns:
        UserProfileResponse 包含用户信息和视频列表。
    """
    return get_user_profile(page, "self", max_videos)


def _extract_user_info_from_ssr(page: Page) -> UserBasicInfo | None:
    """从 SSR RENDER_DATA 提取用户信息。"""
    result = page.evaluate(
        """
        (() => {
            const script = document.querySelector('script#RENDER_DATA');
            if (!script) return '';
            try {
                const decoded = decodeURIComponent(script.textContent);
                const data = JSON.parse(decoded);
                for (const key of Object.keys(data)) {
                    const val = data[key];
                    if (!val || typeof val !== 'object') continue;
                    // 查找 user 或 userInfo 结构
                    for (const subKey of Object.keys(val)) {
                        const sub = val[subKey];
                        if (sub && (sub.uid || sub.sec_uid) && sub.nickname) {
                            return JSON.stringify({
                                uid: sub.uid || '',
                                sec_uid: sub.sec_uid || '',
                                nickname: sub.nickname || '',
                                signature: sub.signature || '',
                                avatar_url: (sub.avatar_larger && sub.avatar_larger.url_list)
                                    ? sub.avatar_larger.url_list[0] || ''
                                    : '',
                                follower_count: sub.follower_count || 0,
                                following_count: sub.following_count || 0,
                                total_favorited: sub.total_favorited || 0,
                                aweme_count: sub.aweme_count || 0,
                                ip_location: sub.ip_location || '',
                            });
                        }
                    }
                }
            } catch(e) {}
            return '';
        })()
        """
    )
    if not result:
        return None

    try:
        data = json.loads(result)
        return UserBasicInfo.from_dict(data)
    except json.JSONDecodeError:
        return None


def _extract_user_info_from_dom(page: Page) -> UserBasicInfo | None:
    """从 DOM 提取用户信息。"""
    result = page.evaluate(
        f"""
        (() => {{
            const container = document.querySelector({json.dumps(USER_INFO_CONTAINER)});
            if (!container) return '';

            // 提取昵称
            const nameEl = container.querySelector(
                'h1, span[class*="name"], span[class*="nickname"]'
            );
            const nickname = nameEl ? nameEl.textContent.trim() : '';

            // 提取签名
            const sigEl = container.querySelector(
                'span[class*="signature"], p[class*="desc"]'
            );
            const signature = sigEl ? sigEl.textContent.trim() : '';

            // 提取粉丝数等
            const fullText = container.textContent;
            let followerCount = 0;
            let followingCount = 0;
            let totalFavorited = 0;

            const followerMatch = fullText.match(/([\\d.]+[万亿wW]?)\\s*粉丝/);
            if (followerMatch) {{
                const num = followerMatch[1];
                if (/[万w]/i.test(num)) {{
                    followerCount = Math.round(parseFloat(num) * 10000);
                }} else if (/亿/.test(num)) {{
                    followerCount = Math.round(parseFloat(num) * 100000000);
                }} else {{
                    followerCount = parseInt(num) || 0;
                }}
            }}

            const followingMatch = fullText.match(/([\\d.]+[万亿wW]?)\\s*关注/);
            if (followingMatch) {{
                const num = followingMatch[1];
                if (/[万w]/i.test(num)) {{
                    followingCount = Math.round(parseFloat(num) * 10000);
                }} else {{
                    followingCount = parseInt(num) || 0;
                }}
            }}

            const likeMatch = fullText.match(/([\\d.]+[万亿wW]?)\\s*获赞/);
            if (likeMatch) {{
                const num = likeMatch[1];
                if (/[万w]/i.test(num)) {{
                    totalFavorited = Math.round(parseFloat(num) * 10000);
                }} else if (/亿/.test(num)) {{
                    totalFavorited = Math.round(parseFloat(num) * 100000000);
                }} else {{
                    totalFavorited = parseInt(num) || 0;
                }}
            }}

            // 提取 IP 属地
            let ipLocation = '';
            const ipMatch = fullText.match(/IP属地[：:]\\s*([\\u4e00-\\u9fa5]+)/);
            if (ipMatch) ipLocation = ipMatch[1];

            return JSON.stringify({{
                nickname: nickname,
                signature: signature,
                follower_count: followerCount,
                following_count: followingCount,
                total_favorited: totalFavorited,
                ip_location: ipLocation,
            }});
        }})()
        """
    )
    if not result:
        return None

    try:
        data = json.loads(result)
        return UserBasicInfo(
            nickname=data.get("nickname", ""),
            signature=data.get("signature", ""),
            follower_count=data.get("follower_count", 0),
            following_count=data.get("following_count", 0),
            total_favorited=data.get("total_favorited", 0),
            ip_location=data.get("ip_location", ""),
        )
    except json.JSONDecodeError:
        return None


def _extract_user_videos_from_ssr(page: Page, max_videos: int) -> list[Feed]:
    """从 SSR RENDER_DATA 提取用户视频列表（支持视频和笔记类型）。"""
    result = page.evaluate(
        f"""
        (() => {{
            const script = document.querySelector('script#RENDER_DATA');
            if (!script) return '';
            try {{
                const decoded = decodeURIComponent(script.textContent);
                const data = JSON.parse(decoded);
                const feeds = [];
                for (const key of Object.keys(data)) {{
                    const val = data[key];
                    if (!val || typeof val !== 'object') continue;
                    for (const subKey of Object.keys(val)) {{
                        const sub = val[subKey];
                        if (Array.isArray(sub)) {{
                            for (const item of sub) {{
                                if (item && (item.aweme_id || (item.aweme_info && item.aweme_info.aweme_id))) {{
                                    const info = item.aweme_info || item;
                                    const stats = info.statistics || {{}};
                                    const author = info.author || {{}};
                                    const awemeType = info.aweme_type || 0;
                                    const videoId = info.aweme_id || '';
                                    const urlPath = awemeType === 68 ? 'note' : 'video';

                                    // 提取笔记图片列表
                                    const images = [];
                                    if (awemeType === 68) {{
                                        const imagePostInfo = info.image_post_info || {{}};
                                        const rawImages = imagePostInfo.images || [];
                                        for (const img of rawImages) {{
                                            const displayImage = (img || {{}}).display_image || {{}};
                                            const urlList = displayImage.url_list || [];
                                            if (urlList.length > 0) images.push(urlList[0]);
                                        }}
                                    }}

                                    const v = info.video || {{}};
                                    const coverList = (v.cover || {{}}).url_list || [];

                                    feeds.push({{
                                        video_id: videoId,
                                        title: info.desc || '',
                                        author: author.nickname || '',
                                        author_id: author.unique_id || author.short_id || '',
                                        author_sec_uid: author.sec_uid || '',
                                        author_signature: author.signature || '',
                                        like_count: stats.digg_count || 0,
                                        comment_count: stats.comment_count || 0,
                                        share_count: stats.share_count || 0,
                                        collect_count: stats.collect_count || 0,
                                        cover_url: coverList[0] || '',
                                        duration: v.duration || 0,
                                        create_time: info.create_time || 0,
                                        url: 'https://www.douyin.com/' + urlPath + '/' + videoId,
                                        aweme_type: awemeType,
                                        images: images,
                                    }});
                                    if (feeds.length >= {max_videos}) break;
                                }}
                            }}
                        }}
                        if (feeds.length >= {max_videos}) break;
                    }}
                    if (feeds.length > 0) break;
                }}
                return JSON.stringify(feeds);
            }} catch(e) {{
                return '';
            }}
        }})()
        """
    )
    if not result:
        return []

    try:
        raw_list = json.loads(result)
        feeds = []
        for raw in raw_list:
            feeds.append(Feed(
                video_id=raw.get("video_id", ""),
                title=raw.get("title", ""),
                author=raw.get("author", ""),
                author_id=raw.get("author_id", ""),
                author_sec_uid=raw.get("author_sec_uid", ""),
                author_signature=raw.get("author_signature", ""),
                like_count=raw.get("like_count", 0),
                comment_count=raw.get("comment_count", 0),
                share_count=raw.get("share_count", 0),
                collect_count=raw.get("collect_count", 0),
                cover_url=raw.get("cover_url", ""),
                duration=raw.get("duration", 0),
                create_time=raw.get("create_time", 0),
                url=raw.get("url", ""),
                aweme_type=raw.get("aweme_type", 0),
                images=raw.get("images") or [],
            ))
        logger.info("SSR 提取到 %d 条用户视频", len(feeds))
        return feeds
    except json.JSONDecodeError:
        return []


def _extract_user_videos_from_dom(page: Page, max_videos: int) -> list[Feed]:
    """从 DOM 提取用户视频列表（支持视频和笔记类型）。"""
    result = page.evaluate(
        f"""
        (() => {{
            const cards = document.querySelectorAll({json.dumps(USER_VIDEO_CARD)});
            if (cards.length === 0) {{
                // 降级：查找所有视频和笔记链接
                const links = document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]');
                const feeds = [];
                const seen = new Set();
                for (const link of links) {{
                    const href = link.href || '';
                    const videoMatch = href.match(/\\/video\\/(\\d+)/);
                    const noteMatch = href.match(/\\/note\\/(\\d+)/);
                    const match = videoMatch || noteMatch;
                    if (!match) continue;
                    const videoId = match[1];
                    if (seen.has(videoId)) continue;
                    seen.add(videoId);

                    const isNote = !!noteMatch;
                    const title = link.textContent.trim().substring(0, 200);
                    const urlPath = isNote ? 'note' : 'video';
                    feeds.push({{
                        video_id: videoId,
                        title: title,
                        author: '',
                        author_id: '',
                        like_count: 0,
                        comment_count: 0,
                        share_count: 0,
                        collect_count: 0,
                        url: 'https://www.douyin.com/' + urlPath + '/' + videoId,
                        aweme_type: isNote ? 68 : 0,
                    }});
                    if (feeds.length >= {max_videos}) break;
                }}
                return JSON.stringify(feeds);
            }}

            const feeds = [];
            for (let i = 0; i < Math.min(cards.length, {max_videos}); i++) {{
                const card = cards[i];
                // 同时查找视频和笔记链接
                const link = card.querySelector('a[href*="/video/"], a[href*="/note/"]');
                let videoId = '';
                let url = '';
                let isNote = false;
                if (link) {{
                    url = link.href;
                    const videoMatch = url.match(/\\/video\\/(\\d+)/);
                    const noteMatch = url.match(/\\/note\\/(\\d+)/);
                    if (videoMatch) {{
                        videoId = videoMatch[1];
                    }} else if (noteMatch) {{
                        videoId = noteMatch[1];
                        isNote = true;
                    }}
                }}

                const titleEl = card.querySelector(
                    '[class*="title"], [class*="desc"], p, span'
                );
                const title = titleEl ? titleEl.textContent.trim() : '';

                let likeCount = 0;
                const fullText = card.textContent;
                const likeMatch = fullText.match(/([\\d]+\\.?[\\d]*)[万w]/i);
                if (likeMatch) {{
                    likeCount = Math.round(parseFloat(likeMatch[1]) * 10000);
                }}

                if (videoId) {{
                    const urlPath = isNote ? 'note' : 'video';
                    feeds.push({{
                        video_id: videoId,
                        title: title,
                        author: '',
                        author_id: '',
                        like_count: likeCount,
                        comment_count: 0,
                        share_count: 0,
                        collect_count: 0,
                        url: url || 'https://www.douyin.com/' + urlPath + '/' + videoId,
                        aweme_type: isNote ? 68 : 0,
                    }});
                }}
            }}
            return JSON.stringify(feeds);
        }})()
        """
    )
    if not result:
        return []

    try:
        raw_list = json.loads(result)
        feeds = []
        for raw in raw_list:
            feeds.append(Feed(
                video_id=raw.get("video_id", ""),
                title=raw.get("title", ""),
                author=raw.get("author", ""),
                author_id=raw.get("author_id", ""),
                like_count=raw.get("like_count", 0),
                comment_count=raw.get("comment_count", 0),
                share_count=raw.get("share_count", 0),
                collect_count=raw.get("collect_count", 0),
                url=raw.get("url", ""),
                aweme_type=raw.get("aweme_type", 0),
            ))
        logger.info("DOM 提取到 %d 条用户视频", len(feeds))
        return feeds
    except json.JSONDecodeError:
        return []
