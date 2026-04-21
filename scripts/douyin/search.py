"""关键词搜索：搜索视频列表、提取搜索结果。"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from urllib.parse import quote

from .cdp import NetworkCapture, Page
from .human import (
    CAPTCHA_GENERIC_MARKERS,
    CAPTCHA_SPECIFIC_MARKERS,
    INACCESSIBLE_KEYWORDS,
    sleep_random,
)
from .selectors import (
    SEARCH_FILTER_BUTTON,
    SEARCH_FILTER_OPTION,
    SEARCH_INPUT,
    SEARCH_RESULT_ITEM,
    SEARCH_SUBMIT,
    SEARCH_TAB_VIDEO,
)
from .types import Feed, FilterOption
from .urls import HOME_URL, make_search_url

logger = logging.getLogger(__name__)

# 抖音搜索 API 的 URL 模式（jingxuan 用 general/search，视频 Tab 用 discover/search）
SEARCH_API_PATTERNS = ("general/search", "discover/search")


def _wait_for_search_results(page: Page, timeout: int = 30) -> None:
    """等待搜索结果页加载完成。

    参考其他 skill：优先检测 #waterFallScrollContainer 或 ul[data-e2e='scroll-list']，
    其次检测 a[href*="/video/"] 视频链接。
    """
    for elapsed in range(timeout):
        time.sleep(1)
        # 策略 1：瀑布流/列表容器（参考 search skill 的 #waterFallScrollContainer）
        container = page.evaluate(
            """
            (() => {
                const sels = [
                    '#waterFallScrollContainer',
                    'ul[data-e2e="scroll-list"]',
                    'div[data-e2e="scroll-child"]'
                ];
                for (const sel of sels) {
                    const el = document.querySelector(sel);
                    if (el && (el.offsetParent !== null || el.offsetWidth > 0))
                        return true;
                }
                return false;
            })()
            """
        )
        if container:
            logger.info("搜索结果容器已加载 (%ds)", elapsed + 1)
            sleep_random(500, 1000)
            return
        # 策略 2：视频链接
        video_link_count = page.evaluate(
            'document.querySelectorAll(\'a[href*="/video/"]\').length'
        )
        if video_link_count and video_link_count > 0:
            logger.info("搜索结果已加载: %d 个视频链接 (%ds)", video_link_count, elapsed + 1)
            sleep_random(500, 1000)
            return
    logger.warning("等待搜索结果超时 (%ds)", timeout)


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
    """检测页面是否不可访问（被封禁/404）。"""
    page_source = page.get_page_source()
    lower = page_source.lower()
    for kw in INACCESSIBLE_KEYWORDS:
        if kw.lower() in lower:
            return True
    return False


def _parse_aweme_from_response(response_data: dict[str, Any]) -> list[Feed]:
    """从 API 响应体中解析 aweme 列表。

    支持 general/search/stream、discover/search 等多种响应结构：
    - data.data[].aweme_info（general/search/stream 综合结果）
    - data.aweme_list[]
    - data.data[]（直接为 aweme 对象）
    - NDJSON 流式响应（每行一个 JSON）
    """
    body = response_data.get("body")
    if not body:
        return []

    if response_data.get("base64Encoded"):
        import base64

        try:
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        except Exception:
            return []

    items: list[dict[str, Any]] = []
    parsed_jsons: list[dict[str, Any]] = []

    # 尝试解析为单个 JSON
    try:
        parsed_jsons.append(json.loads(body))
    except json.JSONDecodeError:
        # 可能是 NDJSON 或 SSE 流（data: {...}）
        for line in body.strip().split("\n"):
            line = line.strip()
            if not line or line == "data: [DONE]":
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                parsed_jsons.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def extract_from(obj: Any) -> None:
        if isinstance(obj, dict):
            if "aweme_info" in obj:
                info = obj["aweme_info"]
                if isinstance(info, dict) and (info.get("aweme_id") or info.get("video_id")):
                    items.append(info)
            elif obj.get("aweme_id") or obj.get("video_id"):
                items.append(obj)
            for v in obj.values():
                extract_from(v)
        elif isinstance(obj, list):
            for v in obj:
                extract_from(v)

    for data in parsed_jsons:
        if not isinstance(data, dict):
            continue
        root = data.get("data", data)
        if isinstance(root, dict):
            for key in ("data", "aweme_list", "item_list"):
                arr = root.get(key)
                if isinstance(arr, list):
                    for item in arr:
                        if isinstance(item, dict):
                            info = item.get("aweme_info", item)
                            if isinstance(info, dict) and (
                                info.get("aweme_id") or info.get("video_id")
                            ):
                                items.append(info)
            extract_from(root)  # 递归提取嵌套结构
        else:
            extract_from(data)

    feeds: list[Feed] = []
    seen: set[str] = set()
    for i, raw in enumerate(items):
        try:
            f = Feed.from_dict(raw)
            if f.video_id and f.video_id not in seen:
                feeds.append(f)
                seen.add(f.video_id)
        except Exception as e:
            continue

    return feeds


def _click_video_tab(page: Page) -> bool:
    """通过 JS 找到视频 Tab 并点击。

    优先使用 data-key="video" 属性（抖音搜索 Tab 的稳定标记），
    降级为文字内容匹配，覆盖综合页（jingxuan）和普通搜索页两种场景。

    Returns:
        True 表示点击成功，False 表示未找到视频 Tab。
    """
    result = page.evaluate(
        """
        (() => {
            // 策略 1：data-key="video" 属性（最稳定，来自实际 DOM 观察）
            // 结构：#search-toolbar-container 内 <span data-key="video">视频</span>
            const byDataKey = document.querySelector('[data-key="video"]');
            if (byDataKey) {
                const rect = byDataKey.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    return {found: true, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, method: 'data-key'};
                }
            }

            // 策略 2：#search-toolbar-container 内文字为"视频"的叶子节点
            const toolbar = document.querySelector('#search-toolbar-container');
            if (toolbar) {
                const allEls = toolbar.querySelectorAll('*');
                for (const el of allEls) {
                    if (el.children.length === 0 && el.textContent.trim() === '视频') {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {found: true, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, method: 'toolbar-text'};
                        }
                    }
                }
            }

            // 策略 3：全局扫描页面顶部 200px 内文字为"视频"的叶子节点
            const allLeafs = document.querySelectorAll('span, a, li, div');
            for (const el of allLeafs) {
                if (el.children.length > 0) continue;
                if (el.textContent.trim() !== '视频') continue;
                const rect = el.getBoundingClientRect();
                if (rect.top < 200 && rect.width > 0 && rect.height > 0) {
                    return {found: true, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, method: 'global-text'};
                }
            }
            return {found: false};
        })()
        """
    )
    if not result or not result.get("found"):
        logger.warning("未找到视频 Tab 按钮（所有策略均失败）")
        return False

    tab_x = result["x"]
    tab_y = result["y"]
    logger.info("找到视频 Tab（方式: %s），坐标: (%.0f, %.0f)，点击", result.get("method"), tab_x, tab_y)
    page.mouse_move(tab_x, tab_y)
    sleep_random(150, 300)
    page.mouse_click(tab_x, tab_y)
    return True

def _switch_to_video_tab_and_capture(page: Page) -> list[Feed]:
    """切换到视频 Tab 并通过 Network 捕获 discover/search API 响应。

    Returns:
        从视频 Tab API 响应中解析出的 Feed 列表，切换失败时返回空列表。
    """
    current_url = page.evaluate("window.location.href") or ""
    if "type=video" in current_url:
        logger.info("当前已在视频 Tab，无需切换")
        return []

    logger.info("切换到视频 Tab，监听 discover/search API")
    with NetworkCapture(page, SEARCH_API_PATTERNS[1], timeout=30) as capture:
        clicked = _click_video_tab(page)
        if not clicked:
            logger.warning("视频 Tab 点击失败，跳过切换")
            return []
        # 等待 URL 变化或 API 响应到达
        _wait_for_url_change(page, "type=video", timeout=5)
        sleep_random(1500, 2500)
        request, response = capture.wait_for_capture()

    if response and response.get("body"):
        feeds = _parse_aweme_from_response(response)
        if feeds:
            logger.info("视频 Tab Network API 捕获 [%s]: %d 条", SEARCH_API_PATTERNS[1], len(feeds))
            return feeds

    logger.info("视频 Tab Network API 未捕获到数据，将依赖 SSR/DOM 提取")
    return []

def _search_via_home_and_capture(page: Page, keyword: str) -> list[Feed]:
    """首页 → 搜索框 → 回车（模拟真实用户，避免直接跳转触发验证）。

    在搜索过程中启用 Network 监听捕获综合页初始数据（general/search）。
    视频 Tab 的切换和捕获由 search_videos 主流程统一负责。
    """
    with NetworkCapture(page, SEARCH_API_PATTERNS[0], timeout=45) as capture:
        _navigate_to_home_and_search(page, keyword)
        request, response = capture.wait_for_capture()

    if response and response.get("body"):
        feeds = _parse_aweme_from_response(response)
        if feeds:
            logger.info("Network API 捕获 [%s]: %d 条", SEARCH_API_PATTERNS[0], len(feeds))
            return feeds

    return []


def _parse_count(count_text: str) -> int:
    """解析数量文本（如 '1.2w' -> 12000）。"""
    if not count_text:
        return 0
    count_text = count_text.strip()
    try:
        if "w" in count_text.lower() or "万" in count_text:
            num_part = re.sub(r"[^\d.]", "", count_text)
            return int(float(num_part) * 10000) if num_part else 0
        if "亿" in count_text:
            num_part = re.sub(r"[^\d.]", "", count_text)
            return int(float(num_part) * 100000000) if num_part else 0
        num_part = re.sub(r"[^\d]", "", count_text)
        return int(num_part) if num_part else 0
    except (ValueError, TypeError):
        return 0


def _navigate_to_home_and_search(page: Page, keyword: str) -> None:
    """先导航到首页，再通过搜索框输入关键词触发搜索。

    模拟真实用户行为：打开首页 → 点击搜索框 → 输入关键词 → 回车搜索。
    比直接跳转搜索 URL 更不容易触发风控。
    """
    current_url = page.evaluate("window.location.href") or ""

    # 如果不在抖音首页，先导航过去
    if "douyin.com" not in current_url or "search" in current_url:
        logger.info("导航到抖音首页")
        page.navigate(HOME_URL)
        # SPA 页面不依赖 loadEventFired，改用轮询等待页面可交互
        _wait_for_page_interactive(page, timeout=15)
        sleep_random(1500, 2500)

    _wait_past_captcha(page)

    # 查找并聚焦搜索框（参考其他 skill：按优先级尝试 data-e2e → placeholder → type）
    focus_result = page.evaluate(
        """
        (() => {
            const sels = [
                'input[data-e2e="searchbar-input"]',
                'input[placeholder*="搜索"]',
                'input[type="text"][maxlength="100"]',
                'input[class*="search-input"]'
            ];
            for (const sel of sels) {
                const input = document.querySelector(sel);
                if (input && (input.offsetParent !== null || input.offsetWidth > 0)) {
                    input.scrollIntoView({block: 'center'});
                    const rect = input.getBoundingClientRect();
                    return {
                        found: true,
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        placeholder: input.placeholder || '',
                        tag: input.tagName
                    };
                }
            }
            return {found: false};
        })()
        """
    )

    if not focus_result or not focus_result.get("found"):
        logger.warning("未找到搜索框，降级为 URL 直接导航")
        search_url = make_search_url(keyword)
        page.navigate(search_url)
        return

    logger.info(
        "找到搜索框: placeholder=%s, 坐标=(%d, %d)",
        focus_result.get("placeholder", ""),
        focus_result.get("x", 0),
        focus_result.get("y", 0),
    )

    # 点击搜索框使其获得焦点
    box_x = focus_result["x"]
    box_y = focus_result["y"]
    page.mouse_move(box_x, box_y)
    sleep_random(100, 200)
    page.mouse_click(box_x, box_y)
    sleep_random(500, 800)

    # 清空搜索框已有内容（与查找逻辑一致的选择器顺序）
    page.evaluate(
        """
        (() => {
            const sels = [
                'input[data-e2e="searchbar-input"]',
                'input[placeholder*="搜索"]',
                'input[type="text"][maxlength="100"]',
                'input[class*="search-input"]'
            ];
            for (const sel of sels) {
                const input = document.querySelector(sel);
                if (input && (input.offsetParent !== null || input.offsetWidth > 0)) {
                    input.focus();
                    input.value = '';
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                }
            }
            return false;
        })()
        """
    )
    sleep_random(200, 400)

    # 逐字符输入关键词（模拟真实打字）
    page.type_text(keyword, delay_ms=80)
    sleep_random(300, 600)

    # 确认输入框中的值
    input_value = page.evaluate(
        """
        (() => {
            const sels = [
                'input[data-e2e="searchbar-input"]',
                'input[placeholder*="搜索"]',
                'input[type="text"][maxlength="100"]',
                'input[class*="search-input"]'
            ];
            for (const sel of sels) {
                const input = document.querySelector(sel);
                if (input && input.value) return input.value;
            }
            return '';
        })()
        """
    )
    logger.info("搜索框当前值: '%s'", input_value)

    # 按回车触发搜索
    page.press_key("Enter")
    sleep_random(500, 800)

    # 等待 URL 变化到搜索结果页
    url_changed = _wait_for_url_change(page, "search", timeout=10)
    if not url_changed:
        # 回车没生效，尝试点击搜索按钮
        logger.warning("回车未触发搜索，尝试点击搜索按钮")
        search_button_clicked = False
        for selector in SEARCH_SUBMIT.split(", "):
            selector = selector.strip()
            if page.has_element(selector):
                page.click_element(selector)
                search_button_clicked = True
                break

        if not search_button_clicked:
            # 最终降级：直接导航到搜索 URL
            logger.warning("搜索按钮也未找到，降级为 URL 直接导航")
            search_url = make_search_url(keyword)
            page.navigate(search_url)
            return

        _wait_for_url_change(page, "search", timeout=10)

    final_url = page.evaluate("window.location.href") or ""
    logger.info("搜索后 URL: %s", final_url)


def _wait_for_page_interactive(page: Page, timeout: int = 15) -> None:
    """等待页面可交互（DOM 中出现关键元素）。"""
    for elapsed in range(timeout):
        time.sleep(1)
        has_header = page.evaluate(
            'document.querySelector("#douyin-header") !== null'
            ' || document.querySelector("header") !== null'
            ' || document.querySelector("#douyin-header-menuCt") !== null'
        )
        if has_header:
            logger.info("首页已可交互 (%ds)", elapsed + 1)
            return
    logger.warning("等待首页可交互超时 (%ds)", timeout)


def _wait_for_url_change(page: Page, expected_keyword: str, timeout: int = 10) -> bool:
    """等待 URL 包含指定关键词。"""
    for _ in range(timeout):
        time.sleep(1)
        current_url = page.evaluate("window.location.href") or ""
        if expected_keyword in current_url:
            return True
    return False


def search_videos(
    page: Page,
    keyword: str,
    max_results: int = 10,
    sort_by: str = "default",
    publish_time: str = "all",
) -> list[Feed]:
    """搜索抖音视频。

    策略优先级：
    1. 首页搜索框输入关键词 + 回车（模拟真实用户行为，避免直接 URL 跳转触发反爬验证）
       同时启用 Network 监听，从搜索 API 响应中提取结构化数据
    2. SSR RENDER_DATA 提取（页面首屏服务端渲染数据）
    3. DOM 提取（从页面元素中解析搜索结果）
    4. 滚动加载更多（fetch/XHR 拦截 + CDP 鼠标滚轮事件）

    ⚠️ 禁止直接导航到 search URL（如 douyin.com/search/xxx?type=video），
    这会触发抖音反爬虫验证。仅在搜索框定位失败时作为最终降级方案。

    Args:
        page: CDP 页面对象。
        keyword: 搜索关键词。
        max_results: 最大结果数。
        sort_by: 排序方式（default/most_liked/latest）。
        publish_time: 发布时间过滤（all/day/week/month/half_year）。

    Returns:
        搜索结果列表。
    """
    logger.info("搜索: %s (max=%d, sort=%s, time=%s)", keyword, max_results, sort_by, publish_time)

    # 策略 1：首页 → 搜索框（模拟真实用户，避免直接跳转触发验证）
    # 同时启用 Network 监听，从综合页 API 响应提取初始数据
    general_feeds = _search_via_home_and_capture(page, keyword)

    _wait_past_captcha(page)

    # 抖音是 SPA，轮询等待视频链接或 RENDER_DATA 出现
    _wait_for_search_results(page, timeout=25)

    if _is_inaccessible(page):
        logger.warning("搜索页不可访问")
        return general_feeds[:max_results] if general_feeds else []

    current_url = page.evaluate("window.location.href") or ""
    logger.info("当前搜索页 URL: %s", current_url)

    # 始终切换到视频 Tab，并通过 Network 捕获视频 Tab 的 API 数据
    # 视频 Tab 的数据比综合页更纯粹，只包含视频内容
    video_tab_feeds = _switch_to_video_tab_and_capture(page)
    _wait_for_search_results(page, timeout=15)

    # 优先使用视频 Tab 的 API 数据，综合页数据作为补充
    api_feeds = video_tab_feeds if video_tab_feeds else general_feeds
    if api_feeds:
        logger.info("Network API 提取到 %d 条（来源: %s）", len(api_feeds),
                    "视频Tab" if video_tab_feeds else "综合页")
        if len(api_feeds) >= max_results:
            return api_feeds[:max_results]

    # 应用筛选条件
    _apply_filters(page, sort_by, publish_time)

    # 提取搜索结果（Network 已提取的作为基础，再补充 SSR/DOM）
    feeds: list[Feed] = list(api_feeds) if api_feeds else []
    existing_ids = {f.video_id for f in feeds if f.video_id}

    # 策略 2：SSR RENDER_DATA
    ssr_feeds = _extract_feeds_from_ssr(page)
    for sf in ssr_feeds or []:
        if sf.video_id and sf.video_id not in existing_ids:
            feeds.append(sf)
            existing_ids.add(sf.video_id)

    # 策略 3：DOM 提取
    if len(feeds) < max_results:
        dom_feeds = _extract_feeds_from_dom(page)
        for df in dom_feeds:
            if df.video_id not in existing_ids:
                feeds.append(df)
                existing_ids.add(df.video_id)

    # 滚动加载更多（双通道：Network API 监听 + DOM 提取）
    if len(feeds) < max_results:
        feeds = _scroll_and_load_more(page, feeds, max_results)

    result = feeds[:max_results]
    logger.info("搜索完成: %d 条结果", len(result))
    return result


def _get_scroll_target_center(page: Page) -> tuple[float, float]:
    """获取搜索结果区域在视口内的中心坐标，用于发送鼠标滚轮事件。

    CDP Input.dispatchMouseEvent 的坐标必须在视口范围内才能生效。
    搜索结果容器通常很高，getBoundingClientRect 的中心点可能超出视口，
    因此需要将 Y 坐标限制在视口可见范围内。

    Returns:
        (center_x, center_y) 坐标元组，保证在视口内。
    """
    result = page.evaluate(
        """
        (() => {
            const viewportHeight = window.innerHeight;
            const viewportWidth = window.innerWidth;
            const candidates = [
                '#search-content-area',
                '#search-result-container',
                '#douyin-right-container',
                '#search-body-container',
            ];
            for (const sel of candidates) {
                const el = document.querySelector(sel);
                if (el && el.offsetWidth > 0) {
                    const rect = el.getBoundingClientRect();
                    const centerX = rect.left + rect.width / 2;
                    // 将 Y 坐标限制在视口内：取容器可见部分的中心
                    const visibleTop = Math.max(rect.top, 0);
                    const visibleBottom = Math.min(rect.bottom, viewportHeight);
                    const centerY = (visibleTop + visibleBottom) / 2;
                    return {
                        x: Math.min(centerX, viewportWidth - 10),
                        y: Math.max(100, Math.min(centerY, viewportHeight - 100)),
                    };
                }
            }
            return { x: 600, y: 400 };
        })()
        """
    )
    return (result.get("x", 600), result.get("y", 400))


def _cdp_wheel_scroll(page: Page, center_x: float, center_y: float, wheel_count: int = 3) -> None:
    """通过 CDP Input.dispatchMouseEvent 发送鼠标滚轮事件触发页面滚动。

    抖音搜索页的 SPA 框架拦截了 window.scrollBy / body.scrollTop 等原生滚动 API，
    只有 CDP 级别的鼠标滚轮事件才能触发真实的滚动和无限加载。

    Args:
        page: CDP 页面对象。
        center_x: 鼠标 X 坐标（搜索结果区域中心）。
        center_y: 鼠标 Y 坐标（搜索结果区域中心）。
        wheel_count: 发送滚轮事件的次数。
    """
    # 先移动鼠标到目标区域（确保事件命中正确的元素）
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


def _inject_fetch_interceptor(page: Page) -> None:
    """注入 fetch/XHR 拦截器，将搜索 API 的响应数据存到全局数组中。

    抖音搜索页的 SPA 框架使用 fetch API 加载更多搜索结果，
    通过拦截 fetch 响应可以可靠地获取到滚动触发的新数据，
    避免 CDP NetworkCapture 与 _send_session 的 WebSocket 竞争问题。
    """
    page.evaluate(
        """
        (() => {
            if (window.__search_interceptor_installed__) return;
            window.__search_interceptor_installed__ = true;
            window.__captured_search_responses__ = [];

            const originalFetch = window.fetch;
            window.fetch = async function(...args) {
                const response = await originalFetch.apply(this, args);
                try {
                    const url = (typeof args[0] === 'string') ? args[0] : args[0]?.url || '';
                    if (url.includes('search') && url.includes('aweme')) {
                        const cloned = response.clone();
                        cloned.text().then(text => {
                            window.__captured_search_responses__.push(text);
                        }).catch(() => {});
                    }
                } catch(e) {}
                return response;
            };

            // 也拦截 XMLHttpRequest
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
                        if (url.includes('search') && url.includes('aweme')) {
                            window.__captured_search_responses__.push(this.responseText);
                        }
                    } catch(e) {}
                });
                return originalXHRSend.apply(this, args);
            };
        })()
        """
    )


def _collect_intercepted_responses(page: Page) -> list[dict[str, Any]]:
    """从页面全局数组中收集并清空拦截到的搜索 API 响应。

    Returns:
        响应数据列表，每个元素为 {"body": "..."} 格式。
    """
    raw_responses = page.evaluate(
        """
        (() => {
            const responses = window.__captured_search_responses__ || [];
            window.__captured_search_responses__ = [];
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


def _is_search_list_end(page: Page) -> bool:
    """检测搜索结果列表是否已滚动到底部。

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


def _scroll_and_load_more(
    page: Page, feeds: list[Feed], max_results: int
) -> list[Feed]:
    """滚动加载更多搜索结果（fetch 拦截 + CDP 鼠标滚轮）。

    核心策略：
    1. 注入 fetch/XHR 拦截器，将搜索 API 响应存到 JS 全局数组
    2. 通过 CDP Input.dispatchMouseEvent(mouseWheel) 触发真实滚动
       （抖音 SPA 拦截了 window.scrollBy，只有 CDP 鼠标滚轮事件才能触发无限加载）
    3. 每次滚动后从全局数组中提取新的 API 响应并解析
    4. 连续 3 次滚动无新增才停止（容忍网络/渲染延迟）

    Args:
        page: CDP 页面对象。
        feeds: 已有的搜索结果列表（会被原地追加）。
        max_results: 最大结果数。

    Returns:
        追加后的完整 feeds 列表。
    """
    max_scroll_attempts = 30
    consecutive_empty = 0
    max_consecutive_empty = 3

    # 获取搜索结果区域中心坐标
    center_x, center_y = _get_scroll_target_center(page)
    logger.info("滚动目标区域中心: (%.0f, %.0f)", center_x, center_y)

    # 注入 fetch/XHR 拦截器
    _inject_fetch_interceptor(page)

    for scroll_attempt in range(1, max_scroll_attempts + 1):
        if len(feeds) >= max_results:
            break

        logger.info(
            "滚动加载第 %d 次 (已获取 %d/%d 条)",
            scroll_attempt, len(feeds), max_results,
        )

        # 用 CDP 鼠标滚轮事件触发滚动（每轮 3 次，deltaY=300，降低频率避免触发风控）
        _cdp_wheel_scroll(page, center_x, center_y, wheel_count=3)
        sleep_random(3500, 5500)

        existing_ids = {f.video_id for f in feeds if f.video_id}
        added = 0

        # 从拦截器中收集新的 API 响应
        intercepted = _collect_intercepted_responses(page)
        if intercepted:
            logger.info("拦截到 %d 个搜索 API 响应", len(intercepted))

        for response_data in intercepted:
            api_feeds = _parse_aweme_from_response(response_data)
            for af in api_feeds:
                if af.video_id and af.video_id not in existing_ids:
                    feeds.append(af)
                    existing_ids.add(af.video_id)
                    added += 1
            if api_feeds:
                logger.info("API 响应解析出 %d 条候选", len(api_feeds))

        # 补充：从 DOM 提取（覆盖拦截器未捕获的场景）
        dom_feeds = _extract_feeds_from_dom(page)
        for df in dom_feeds:
            if df.video_id and df.video_id not in existing_ids:
                feeds.append(df)
                existing_ids.add(df.video_id)
                added += 1

        if added > 0:
            consecutive_empty = 0
            logger.info("本次滚动新增 %d 条, 累计 %d 条", added, len(feeds))
        else:
            consecutive_empty += 1
            if _is_captcha_page(page):
                _wait_past_captcha(page)
                consecutive_empty = 0
            elif consecutive_empty >= max_consecutive_empty:
                logger.info(
                    "连续 %d 次滚动无新增, 停止加载", max_consecutive_empty
                )
                break

        # 每次滚动后都检测底部提示，一旦出现立即停止（不依赖 class，直接匹配文字）
        if _is_search_list_end(page):
            logger.info("检测到搜索结果列表底部提示，停止加载")
            break

    return feeds


def _apply_filters(page: Page, sort_by: str, publish_time: str) -> None:
    """应用搜索筛选条件（参考其他 skill：data-index 属性、#search-toolbar-container）。"""
    if sort_by == "default" and publish_time == "all":
        return

    # 点击筛选按钮
    if page.has_element(SEARCH_FILTER_BUTTON):
        page.click_element(SEARCH_FILTER_BUTTON)
        sleep_random(500, 800)

    sort_map = {
        "most_liked": "最多点赞",
        "latest": "最新发布",
    }
    time_map = {
        "day": "一天内",
        "week": "一周内",
        "month": "一个月内",
        "half_year": "半年内",
    }

    # 选择排序
    if sort_by in sort_map:
        _click_filter_option(page, sort_map[sort_by])

    # 选择时间
    if publish_time in time_map:
        _click_filter_option(page, time_map[publish_time])

    sleep_random(800, 1200)
    page.wait_dom_stable()


def _click_filter_option(page: Page, option_text: str) -> None:
    """点击筛选选项（参考其他 skill：最新发布用 data-index1/2 属性）。"""
    # 最新发布：优先用 data-index 属性（参考 search_tool_bar.html）
    if option_text == "最新发布":
        result = page.evaluate(
            """
            (() => {
                const el = document.querySelector(
                    '#search-toolbar-container span[data-index1="0"][data-index2="1"]'
                );
                if (el) {
                    const rect = el.getBoundingClientRect();
                    return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                }
                return null;
            })()
            """
        )
        if result:
            page.mouse_move(result["x"], result["y"])
            sleep_random(100, 200)
            page.mouse_click(result["x"], result["y"])
            sleep_random(300, 500)
            return

    result = page.evaluate(
        f"""
        (() => {{
            const options = document.querySelectorAll({json.dumps(SEARCH_FILTER_OPTION)});
            for (const opt of options) {{
                if (opt.textContent.trim().includes({json.dumps(option_text)})) {{
                    const rect = opt.getBoundingClientRect();
                    return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
                }}
            }}
            return null;
        }})()
        """
    )
    if result:
        page.mouse_move(result["x"], result["y"])
        sleep_random(100, 200)
        page.mouse_click(result["x"], result["y"])
        sleep_random(300, 500)


def _extract_feeds_from_ssr(page: Page) -> list[Feed]:
    """从 SSR RENDER_DATA 提取搜索结果。"""
    result = page.evaluate(
        """
        (() => {
            const script = document.querySelector('script#RENDER_DATA');
            if (!script) return '';
            try {
                const decoded = decodeURIComponent(script.textContent);
                const data = JSON.parse(decoded);
                const feeds = [];
                for (const key of Object.keys(data)) {
                    const val = data[key];
                    if (!val || typeof val !== 'object') continue;
                    for (const subKey of Object.keys(val)) {
                        const sub = val[subKey];
                        if (Array.isArray(sub)) {
                            for (const item of sub) {
                                if (item && item.aweme_info) {
                                    const info = item.aweme_info;
                                    const stats = info.statistics || {};
                                    const author = info.author || {};
                                    const v = info.video || {};
                                    const coverList = (v.cover || {}).url_list || [];
                                    const awemeType = info.aweme_type || 0;
                                    const videoId = info.aweme_id || '';
                                    const urlPath = awemeType === 68 ? 'note' : 'video';

                                    // 提取笔记图片列表
                                    const images = [];
                                    if (awemeType === 68) {
                                        const imagePostInfo = info.image_post_info || {};
                                        const rawImages = imagePostInfo.images || [];
                                        for (const img of rawImages) {
                                            const displayImage = (img || {}).display_image || {};
                                            const urlList = displayImage.url_list || [];
                                            if (urlList.length > 0) images.push(urlList[0]);
                                        }
                                    }

                                    feeds.push({
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
                                    });
                                }
                            }
                        }
                    }
                }
                return JSON.stringify(feeds);
            } catch(e) {
                return '';
            }
        })()
        """
    )
    if not result:
        logger.info("[SSR 排查] 主提取返回空（无 RENDER_DATA 或解析失败）")
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
        if feeds:
            f0 = feeds[0]
            logger.info(
                "[SSR 排查] 首条解析后: like=%d comment=%d share=%d",
                f0.like_count,
                f0.comment_count,
                f0.share_count,
            )
        logger.info("SSR 提取到 %d 条搜索结果", len(feeds))
        return feeds
    except json.JSONDecodeError as e:
        logger.warning("[SSR 排查] feeds JSON 解析失败: %s", e)
        return []


def _extract_feeds_from_dom(page: Page) -> list[Feed]:
    """从 DOM 提取搜索结果。

    两种策略：
    1. 通过 SEARCH_RESULT_ITEM 选择器定位结果项容器
    2. 降级：直接遍历所有 a[href*="/video/"] 链接
    """
    result = page.evaluate(
        f"""
        (() => {{
            // 策略 1：通过结果项容器提取
            let items = document.querySelectorAll({json.dumps(SEARCH_RESULT_ITEM)});

            // 策略 2 降级：如果容器选择器失效，直接找所有视频链接的父级 li
            if (items.length === 0) {{
                items = document.querySelectorAll('ul[data-e2e="scroll-list"] > li');
            }}

            if (items.length === 0) return '[]';

            const feeds = [];
            const seenIds = new Set();

            for (const item of items) {{
                // 同时查找视频和笔记链接
                const link = item.querySelector('a[href*="/video/"], a[href*="/note/"]');
                if (!link) continue;

                const url = link.href;
                const videoMatch = url.match(/\\/video\\/(\\d+)/);
                const noteMatch = url.match(/\\/note\\/(\\d+)/);
                const idMatch = videoMatch || noteMatch;
                if (!idMatch) continue;

                const videoId = idMatch[1];
                if (seenIds.has(videoId)) continue;
                seenIds.add(videoId);
                // 视频 Tab 下兜底为视频类型，不依赖 URL 路径判断
                // 抖音部分视频在综合页会用 /note/ 路径展示，实际是视频内容
                const isNote = false;

                // 从链接文本解析信息
                // 格式: "时长 点赞数 标题内容 @作者 时间"
                // 例: "02:35 22.8万 浅感受一下Ai绘画的效果 @超超在家 2年前"
                const linkText = link.textContent.trim();

                // 解析点赞数（支持 "22.8万"、"1.7万"、"307" 等格式）
                let likeCount = 0;
                const likeWanMatch = linkText.match(/(\\d+\\.?\\d*)[万w]/i);
                if (likeWanMatch) {{
                    likeCount = Math.round(parseFloat(likeWanMatch[1]) * 10000);
                }} else {{
                    // 匹配时长后紧跟的纯数字（如 "02:35 307 标题"）
                    const likeNumMatch = linkText.match(/\\d+:\\d+[\\s]*(\\d+)/);
                    if (likeNumMatch) {{
                        likeCount = parseInt(likeNumMatch[1], 10);
                    }}
                }}

                // 提取标题：优先从专用元素获取，降级从链接文本解析
                let title = '';
                const titleEl = item.querySelector(
                    'h2, [class*="title"], [class*="desc"], p[class*="multi-content"]'
                );
                if (titleEl && titleEl.textContent.trim().length > 3) {{
                    title = titleEl.textContent.trim();
                }} else {{
                    // 从链接文本中提取：去掉开头的 "合集"/"时长"/"点赞数"，去掉末尾的 "@作者 时间"
                    let cleaned = linkText;
                    // 去掉开头的 "合集" 标记
                    cleaned = cleaned.replace(/^合集/, '');
                    // 去掉时长 (如 "02:35" 或 "10:09")
                    cleaned = cleaned.replace(/^\\d{{1,2}}:\\d{{2}}/, '');
                    // 去掉点赞数 (如 "22.8万" 或 "307")
                    cleaned = cleaned.replace(/^\\d+\\.?\\d*[万w]?/i, '');
                    // 去掉末尾的 "@作者 时间" 部分
                    cleaned = cleaned.replace(/@[^@]+?\\d+[天周月年]前$/, '');
                    cleaned = cleaned.replace(/@[^@]+?\\d+小时前$/, '');
                    title = cleaned.trim();
                }}

                // 提取作者
                let author = '';
                const authorEl = item.querySelector(
                    '[class*="author"], [class*="nickname"], span[class*="name"]'
                );
                if (authorEl && authorEl.textContent.trim()) {{
                    author = authorEl.textContent.trim();
                }} else {{
                    // 从链接文本中提取 @作者
                    const authorMatch = linkText.match(/@([^@]+?)(?:\\d+[天周月年小时]+前|$)/);
                    if (authorMatch) {{
                        author = authorMatch[1].trim();
                    }}
                }}

                if (videoId && (title || linkText)) {{
                    // 统一将 /note/ 路径替换为 /video/，避免视频被用笔记地址打开
                    const normalizedUrl = url.replace('/note/', '/video/');
                    feeds.push({{
                        video_id: videoId,
                        title: title || linkText.substring(0, 80),
                        author: author,
                        author_id: '',
                        author_sec_uid: '',
                        author_signature: '',
                        like_count: likeCount,
                        comment_count: 0,
                        share_count: 0,
                        collect_count: 0,
                        cover_url: '',
                        duration: 0,
                        url: normalizedUrl,
                        aweme_type: 0,
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
                author_sec_uid=raw.get("author_sec_uid", ""),
                author_signature=raw.get("author_signature", ""),
                like_count=raw.get("like_count", 0),
                comment_count=raw.get("comment_count", 0),
                share_count=raw.get("share_count", 0),
                collect_count=raw.get("collect_count", 0),
                cover_url=raw.get("cover_url", ""),
                duration=raw.get("duration", 0),
                url=raw.get("url", ""),
                aweme_type=raw.get("aweme_type", 0),
            ))
        logger.info("DOM 提取到 %d 条搜索结果", len(feeds))
        return feeds
    except json.JSONDecodeError:
        return []


def get_filter_options(page: Page) -> list[FilterOption]:
    """获取当前搜索页的筛选选项。"""
    result = page.evaluate(
        f"""
        (() => {{
            const options = document.querySelectorAll({json.dumps(SEARCH_FILTER_OPTION)});
            const result = [];
            for (const opt of options) {{
                const text = opt.textContent.trim();
                const isActive = opt.classList.contains('active')
                    || opt.getAttribute('aria-selected') === 'true'
                    || opt.querySelector('.active') !== null;
                if (text) {{
                    result.push({{label: text, active: isActive}});
                }}
            }}
            return JSON.stringify(result);
        }})()
        """
    )
    if not result:
        return []

    try:
        raw_list = json.loads(result)
        return [
            FilterOption(label=r["label"], active=r.get("active", False))
            for r in raw_list
        ]
    except json.JSONDecodeError:
        return []
