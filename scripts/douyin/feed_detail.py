"""视频详情页：评论抓取、视频信息提取。

策略优先级：
- 视频详情：拦截（NetworkCapture）→ SSR → DOM
- 评论：拦截（滚动触发）→ 源码正则 → DOM → 主动 fetch（兜底）
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .cdp import NetworkCapture, Page
from .human import (
    CAPTCHA_GENERIC_MARKERS,
    CAPTCHA_SPECIFIC_MARKERS,
    sleep_random,
)
from .selectors import COMMENT_END_CONTAINER, PARENT_COMMENT

logger = logging.getLogger(__name__)

# 拦截用 URL 模式
DETAIL_API_PATTERN = "aweme/v1/web/aweme/detail"
COMMENT_API_PATTERN = "aweme/v1/web/comment/list"


def _wait_for_video_page(page: Page, video_url: str, timeout: int = 20) -> None:
    """等待视频/笔记详情页加载完成（轮询检测 RENDER_DATA 或内容出现）。

    抖音是 SPA，导航不触发 Page.loadEventFired，
    因此改用轮询检测 script#RENDER_DATA、__ROUTER_DATA__ 或 video/图文元素。
    """
    is_note = _is_note_url(video_url)
    poll_interval = 0.5
    for elapsed in range(int(timeout / poll_interval)):
        time.sleep(poll_interval)
        has_render_data = page.evaluate(
            "!!document.querySelector('script#RENDER_DATA')"
        )
        has_router_data = page.evaluate(
            "!!(window.__ROUTER_DATA__ && window.__ROUTER_DATA__.loaderData)"
        )
        if is_note:
            has_content = page.evaluate(
                "!!document.querySelector('img[class*=\"note\"]') "
                "|| !!document.querySelector('[class*=\"note-content\"]') "
                "|| !!document.querySelector('a[href*=\"/note/\"]') "
                "|| !!document.querySelector('[class*=\"image-list\"]')"
            )
        else:
            has_content = page.evaluate(
                "!!document.querySelector('video') || !!document.querySelector('a[href*=\"/video/\"]')"
            )
        if has_render_data or has_router_data or has_content:
            waited = round((elapsed + 1) * poll_interval, 1)
            page_type = "笔记" if is_note else "视频"
            logger.info("%s详情页已加载 (%.1fs): %s", page_type, waited, video_url[:60])
            page.wait_dom_stable(check_interval=0.3, stable_count=2)
            return
    logger.warning("等待详情页超时 (%.1fs): %s", timeout, video_url[:60])


def _is_captcha_page(page: Page) -> bool:
    """检测是否为验证码页面或验证弹窗。"""
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


def _extract_aweme_id(video_url: str) -> str:
    """从视频/笔记 URL 中提取 aweme_id。"""
    m = re.search(r"/video/(\d+)", video_url)
    if m:
        return m.group(1)
    m = re.search(r"/note/(\d+)", video_url)
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d+)", video_url)
    if m:
        return m.group(1)
    return ""

def _is_note_url(url: str) -> bool:
    """判断 URL 是否为笔记（图文）类型。"""
    return "/note/" in url


def _traditional_to_simplified(text: str) -> str:
    """简繁转换（常用字）。"""
    _map = {
        "強": "强", "別": "别", "風": "风", "裝": "装", "舉": "举", "麼": "么",
        "當": "当", "視": "视", "頻": "频", "還": "还", "試": "试", "後": "后",
        "給": "给", "電": "电", "腦": "脑", "軟": "软", "裡": "里",
        "這": "这", "個": "个", "務": "务", "為": "为", "東": "东",
        "開": "开", "體": "体", "驗": "验", "點": "点", "擊": "击", "關": "关",
        "註": "注", "讚": "赞", "評": "评", "論": "论", "轉": "转", "發": "发",
    }
    for k, v in _map.items():
        text = text.replace(k, v)
    return text


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


def _parse_douyin_response(body: str | bytes) -> dict[str, Any]:
    """解析抖音 API 响应（支持 }xxx{ 分隔的多块 JSON）。"""
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = str(body)

    decoder = json.JSONDecoder()
    parsed_blocks: list[dict[str, Any]] = []

    # 查找 }xxx{ 分隔符
    separators = list(re.finditer(r"\}[a-z0-9\s]+\{", text))
    if separators:
        split_points = [0]
        for sep in separators:
            split_points.append(sep.start() + 1)
        split_points.append(len(text))
        for i in range(len(split_points) - 1):
            segment = text[split_points[i] : split_points[i + 1]]
            json_start = segment.find("{")
            if json_start == -1:
                continue
            try:
                obj, _ = decoder.raw_decode(segment, json_start)
                parsed_blocks.append(obj)
            except json.JSONDecodeError:
                pass
    else:
        pos = 0
        while pos < len(text):
            json_start = -1
            for i in range(pos, len(text)):
                if text[i] in ("{", "["):
                    json_start = i
                    break
            if json_start == -1:
                break
            try:
                obj, end_idx = decoder.raw_decode(text, json_start)
                parsed_blocks.append(obj)
                pos = json_start + end_idx
            except json.JSONDecodeError:
                pos = json_start + 1
                if pos >= len(text):
                    break

    if not parsed_blocks:
        raise ValueError("响应体中未找到有效 JSON")

    if len(parsed_blocks) == 1:
        return parsed_blocks[0]
    # 多块合并：优先取含 aweme_detail 或 comments 的块
    for block in parsed_blocks:
        if "aweme_detail" in block or "aweme_info" in block:
            return block
        if "comments" in block and isinstance(block.get("comments"), list):
            return block
    return parsed_blocks[0]


def detect_aweme_type(page: Page, aweme_id: str) -> dict[str, Any]:
    """通过 API 探测 aweme 的类型（视频/笔记），不导航页面。

    在浏览器上下文中 fetch 抖音详情 API，仅提取 aweme_type 等基本信息。
    不会触发页面导航，复用当前页面的 cookie/session，风险低。

    Args:
        page: 已连接的浏览器页面（需已在 douyin.com 域下）。
        aweme_id: 作品 ID。

    Returns:
        dict 包含:
        - aweme_type: int (0=视频, 68=笔记/图文, -1=未知)
        - is_note: bool
        - aweme_id: str
        - title: str (作品标题)
        - detail_url: str (正确的详情页 URL)
        - success: bool
        - error: str | None
    """
    api_url = (
        f"https://www.douyin.com/aweme/v1/web/aweme/detail/"
        f"?aweme_id={aweme_id}&device_platform=webapp"
    )

    fetch_js = f"""
    (() => {{
        return new Promise((resolve) => {{
            fetch({json.dumps(api_url)}, {{
                method: "GET",
                credentials: "include",
                headers: {{
                    "Accept": "application/json",
                    "Referer": "https://www.douyin.com/video/{aweme_id}"
                }}
            }})
            .then(r => r.text())
            .then(resolve)
            .catch(e => resolve(JSON.stringify({{"error": e.message}})));
        }});
    }})()
    """

    try:
        response_text = page.evaluate_async(fetch_js, timeout=15.0)
        if not response_text:
            return {
                "aweme_type": -1,
                "is_note": False,
                "aweme_id": aweme_id,
                "title": "",
                "detail_url": f"https://www.douyin.com/video/{aweme_id}",
                "success": False,
                "error": "API 无响应",
            }

        data = json.loads(response_text)
        if data.get("error") or data.get("status_code", -1) != 0:
            error_msg = data.get("error") or f"status_code={data.get('status_code')}"
            return {
                "aweme_type": -1,
                "is_note": False,
                "aweme_id": aweme_id,
                "title": "",
                "detail_url": f"https://www.douyin.com/video/{aweme_id}",
                "success": False,
                "error": error_msg,
            }

        aweme = data.get("aweme_detail") or data.get("aweme_info")
        if not aweme:
            return {
                "aweme_type": -1,
                "is_note": False,
                "aweme_id": aweme_id,
                "title": "",
                "detail_url": f"https://www.douyin.com/video/{aweme_id}",
                "success": False,
                "error": "响应中无 aweme_detail",
            }

        aweme_type = aweme.get("aweme_type", 0)
        is_note = aweme_type == 68
        url_path = "note" if is_note else "video"
        title = aweme.get("desc", "")

        logger.info(
            "detect_aweme_type: id=%s, type=%d (%s), title=%s",
            aweme_id, aweme_type, "笔记" if is_note else "视频", title[:30],
        )

        return {
            "aweme_type": aweme_type,
            "is_note": is_note,
            "aweme_id": aweme_id,
            "title": title,
            "detail_url": f"https://www.douyin.com/{url_path}/{aweme_id}",
            "success": True,
            "error": None,
        }
    except (json.JSONDecodeError, TypeError) as exc:
        logger.debug("detect_aweme_type 解析失败: %s", exc)
        return {
            "aweme_type": -1,
            "is_note": False,
            "aweme_id": aweme_id,
            "title": "",
            "detail_url": f"https://www.douyin.com/video/{aweme_id}",
            "success": False,
            "error": str(exc),
        }


def get_video_info(
    page: Page,
    video_url: str,
    *,
    fetch_comments_parallel: int | None = None,
) -> dict[str, Any] | tuple[dict[str, Any], list[dict[str, Any]]]:
    """获取视频详情信息。

    策略优先级：
    1. 拦截（NetworkCapture，被动获取页面请求的响应）
    2. RENDER_DATA / __ROUTER_DATA__ / __INITIAL_STATE__
    3. DOM 提取

    若 fetch_comments_parallel 为整数，则在页面就绪后并行抓取评论，返回 (video_info, comments)。
    """
    aweme_id = _extract_aweme_id(video_url)
    comments_result: list[dict[str, Any]] | None = None

    # 策略 1：拦截（在 navigate 前启用，页面加载时被动捕获）
    # 超时设为 30s：抖音 SSR 页面加载后，前端 JS 可能延迟数秒才发出 detail API 请求
    with NetworkCapture(page, DETAIL_API_PATTERN, timeout=30) as capture:
        _t_start = time.monotonic()
        page.navigate(video_url)
        _wait_for_video_page(page, video_url)
        _t1 = time.monotonic()
        logger.info("[耗时] get_video_info: navigate+wait_page: %.1fs", _t1 - _t_start)

        # 页面就绪后，fetch_comments 即可开始（不依赖 video_info 拦截）
        if fetch_comments_parallel is not None:
            from concurrent.futures import ThreadPoolExecutor

            def _do_comments() -> list[dict[str, Any]]:
                _tc = time.monotonic()
                r = fetch_comments(
                    page, video_url, max_comments=fetch_comments_parallel
                )
                logger.info("[耗时] fetch_comments: %.1fs", time.monotonic() - _tc)
                return r

            with ThreadPoolExecutor(max_workers=1) as ex:
                future_comments = ex.submit(_do_comments)
                sleep_random(800, 1500)
                _wait_past_captcha(page)
                _t2 = time.monotonic()
                logger.info("[耗时] get_video_info: sleep+captcha: %.1fs", _t2 - _t1)

                request, response = capture.wait_for_capture()
                _t3 = time.monotonic()
                logger.info("[耗时] get_video_info: wait_for_capture: %.1fs", _t3 - _t2)
                comments_result = future_comments.result()
        else:
            sleep_random(800, 1500)
            _wait_past_captcha(page)
            _t2 = time.monotonic()
            logger.info("[耗时] get_video_info: sleep+captcha: %.1fs", _t2 - _t1)

            request, response = capture.wait_for_capture()
            _t3 = time.monotonic()
            logger.info("[耗时] get_video_info: wait_for_capture: %.1fs", _t3 - _t2)

    def _wrap(info: dict[str, Any]) -> dict[str, Any] | tuple[dict[str, Any], list[dict[str, Any]]]:
        if fetch_comments_parallel is not None and comments_result is not None:
            return (info, comments_result)
        return info

    if request and response:
        info = _extract_video_info_from_intercepted(response, aweme_id, video_url)
        if info:
            logger.info("视频详情：拦截成功")
            return _wrap(info)

    # 策略 2：从 RENDER_DATA / __ROUTER_DATA__ / __INITIAL_STATE__ 提取
    info = _extract_video_info_from_ssr(page, video_url)
    if info:
        return _wrap(info)

    # 策略 3：从 DOM 提取
    dom_info = _extract_video_info_from_dom(page)
    if dom_info:
        return _wrap(dom_info)

    return _wrap({
        "video_id": aweme_id,
        "title": page.get_page_title() or "",
        "author": "",
        "author_id": "",
        "author_sec_uid": "",
        "author_signature": "",
        "url": video_url,
        "like_count": 0,
        "comment_count": 0,
        "share_count": 0,
        "collect_count": 0,
    })


def _extract_images_from_aweme(aweme: dict[str, Any]) -> list[str]:
    """从 aweme 对象提取笔记图片 URL 列表。

    抖音笔记的图片数据路径：aweme.image_post_info.images[].display_image.url_list
    """
    images: list[str] = []
    image_post_info = aweme.get("image_post_info") or {}
    raw_images = image_post_info.get("images") or []
    for img in raw_images:
        if not isinstance(img, dict):
            continue
        display_image = img.get("display_image") or {}
        url_list = display_image.get("url_list") or []
        if url_list:
            images.append(url_list[0])
    return images

def _build_aweme_info_dict(
    aweme: dict[str, Any],
    aweme_id: str,
    video_url: str,
) -> dict[str, Any]:
    """从 aweme 对象构建统一的信息字典（视频和笔记通用）。"""
    stats = aweme.get("statistics", {})
    author = aweme.get("author", {})
    video_obj = aweme.get("video") or {}
    aweme_type = aweme.get("aweme_type", 0)
    cover_url = ((video_obj.get("cover") or {}).get("url_list") or [""])[0]
    duration = video_obj.get("duration", 0)
    play_url = _extract_play_url_from_video(video_obj)

    # 根据 aweme_type 决定 URL 路径
    if aweme_type == 68:
        url = f"https://www.douyin.com/note/{aweme_id}"
    else:
        url = video_url or f"https://www.douyin.com/video/{aweme_id}"

    info: dict[str, Any] = {
        "video_id": aweme_id,
        "title": aweme.get("desc", ""),
        "author": author.get("nickname", "") if isinstance(author, dict) else "",
        "author_id": (
            author.get("unique_id", "") or author.get("short_id", "")
            if isinstance(author, dict)
            else ""
        ),
        "author_sec_uid": author.get("sec_uid", "") if isinstance(author, dict) else "",
        "author_signature": author.get("signature", "") if isinstance(author, dict) else "",
        "like_count": stats.get("digg_count", 0),
        "comment_count": stats.get("comment_count", 0),
        "share_count": stats.get("share_count", 0),
        "collect_count": stats.get("collect_count", 0),
        "cover_url": cover_url,
        "create_time": aweme.get("create_time", 0),
        "duration": duration,
        "url": url,
        "play_url": play_url,
        "aweme_type": aweme_type,
    }

    # 笔记类型：提取图片列表
    if aweme_type == 68:
        images = _extract_images_from_aweme(aweme)
        if images:
            info["images"] = images
            logger.info("笔记图片提取成功，共 %d 张", len(images))

    return info

def _extract_play_url_from_video(video_obj: dict[str, Any]) -> str | None:
    """从 video 对象提取视频直链（play_addr / download_addr）。"""
    if not video_obj:
        return None
    play_addr = video_obj.get("play_addr") or {}
    url_list = play_addr.get("url_list") or []
    if url_list:
        return url_list[0]
    bit_rate = video_obj.get("bit_rate")
    if bit_rate:
        first = bit_rate[0] if isinstance(bit_rate, list) else bit_rate
        pa = (first or {}).get("play_addr") or {}
        ul = pa.get("url_list") or []
        if ul:
            return ul[0]
    dl_addr = video_obj.get("download_addr") or {}
    ul = dl_addr.get("url_list") or []
    return ul[0] if ul else None


def _collect_keys_containing(obj: Any, needle: str, prefix: str = "", depth: int = 0, max_depth: int = 10) -> list[str]:
    """递归收集包含 needle 的 key 路径（用于调试）。"""
    if depth > max_depth or obj is None:
        return []
    out: list[str] = []
    needle_lower = needle.lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if needle_lower in k.lower():
                out.append(p)
            out.extend(_collect_keys_containing(v, needle, p, depth + 1, max_depth))
    elif isinstance(obj, list) and obj:
        out.extend(_collect_keys_containing(obj[0], needle, f"{prefix}[0]", depth + 1, max_depth))
    return out


def _find_subtitle_infos_in_obj(obj: Any, depth: int = 0) -> list[dict] | None:
    """递归查找 obj 中的 subtitle_infos（抖音可能在 aweme.video 或 aweme 下）。"""
    if depth > 15 or obj is None:
        return None
    if isinstance(obj, dict):
        infos = obj.get("subtitle_infos")
        if infos and isinstance(infos, list) and len(infos) > 0:
            return infos
        for v in obj.values():
            found = _find_subtitle_infos_in_obj(v, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_subtitle_infos_in_obj(item, depth + 1)
            if found:
                return found
    return None


def _extract_video_info_from_intercepted(
    response: dict[str, Any],
    aweme_id: str,
    video_url: str,
) -> dict[str, Any] | None:
    """从拦截的响应中提取视频/笔记详情。"""
    body = response.get("body")
    if not body:
        return None
    try:
        data = _parse_douyin_response(body)
    except (ValueError, json.JSONDecodeError) as e:
        logger.debug("拦截响应解析失败: %s", e)
        return None
    if data.get("status_code", -1) != 0:
        return None
    aweme = data.get("aweme_detail") or data.get("aweme_info")
    if not aweme or str(aweme.get("aweme_id", "")) != aweme_id:
        return None

    info = _build_aweme_info_dict(aweme, aweme_id, video_url)

    # 视频类型：从 aweme 中提取 subtitle_infos（供字幕提取优先使用）
    if info.get("aweme_type", 0) != 68:
        subtitle_infos = _find_subtitle_infos_in_obj(aweme)
        if subtitle_infos:
            info["subtitle_infos"] = subtitle_infos
            logger.info("aweme 响应中含 subtitle_infos，条数: %d", len(subtitle_infos))
        else:
            sub_keys = _collect_keys_containing(aweme, "subtitle", max_depth=5)
            if sub_keys:
                logger.debug("aweme 中字幕相关路径: %s", sub_keys)

    return info



def _extract_video_info_from_dom(page: Page) -> dict[str, Any] | None:
    """从 DOM 元素提取视频信息（点赞、评论等可见数据）。"""
    aweme_id = ""
    m = re.search(r"/video/(\d+)", page.get_current_url() or "")
    if m:
        aweme_id = m.group(1)

    result = page.evaluate(
        """
        (() => {
            const info = {
                video_id: '',
                title: document.title || '',
                author: '',
                like_count: 0,
                comment_count: 0,
                share_count: 0,
                collect_count: 0,
            };

            // 解析数量文本 (1.2万 -> 12000, 307 -> 307)
            function parseCount(text) {
                if (!text) return 0;
                text = String(text).trim().replace(/,/g, '');
                const wan = text.match(/([\\d.]+)[万w]/i);
                if (wan) return Math.round(parseFloat(wan[1]) * 10000);
                const num = text.replace(/[^\\d]/g, '');
                return num ? parseInt(num, 10) : 0;
            }

            // 查找包含数字的 span/div（点赞、评论、分享、收藏）
            const allText = document.body.innerText || '';
            const likeMatch = allText.match(/([\\d.,]+[万w]?)\\s*赞|赞\\s*([\\d.,]+[万w]?)/i)
                || allText.match(/([\\d.,]+[万w]?)\\s*喜欢|喜欢\\s*([\\d.,]+[万w]?)/i);
            if (likeMatch) {
                info.like_count = parseCount(likeMatch[1] || likeMatch[2]);
            }

            const commentMatch = allText.match(/([\\d.,]+[万w]?)\\s*评论|评论\\s*([\\d.,]+[万w]?)/i);
            if (commentMatch) {
                info.comment_count = parseCount(commentMatch[1] || commentMatch[2]);
            }

            const shareMatch = allText.match(/([\\d.,]+[万w]?)\\s*分享|分享\\s*([\\d.,]+[万w]?)/i);
            if (shareMatch) {
                info.share_count = parseCount(shareMatch[1] || shareMatch[2]);
            }

            const collectMatch = allText.match(/([\\d.,]+[万w]?)\\s*收藏|收藏\\s*([\\d.,]+[万w]?)/i);
            if (collectMatch) {
                info.collect_count = parseCount(collectMatch[1] || collectMatch[2]);
            }

            // 尝试从 data-e2e 或 class 定位
            const selectors = [
                '[data-e2e="like-count"], [data-e2e="digg-count"]',
                '[class*="likeCount"], [class*="diggCount"]',
                '[class*="LikeCount"], [class*="DiggCount"]',
            ];
            for (const sel of selectors) {
                const el = document.querySelector(sel);
                if (el && !info.like_count) {
                    info.like_count = parseCount(el.textContent);
                    if (info.like_count) break;
                }
            }

            return JSON.stringify(info);
        })()
        """
    )
    if not result:
        return None
    try:
        info = json.loads(result)
        if info.get("video_id") or aweme_id:
            info["video_id"] = info.get("video_id") or aweme_id
            info["url"] = f"https://www.douyin.com/video/{info['video_id']}"
            return info
    except json.JSONDecodeError:
        pass
    return None


def _extract_video_info_from_ssr(page: Page, video_url: str = "") -> dict[str, Any] | None:
    """从 SSR RENDER_DATA / __ROUTER_DATA__ / __INITIAL_STATE__ 提取视频信息。

    支持多种抖音页面数据结构，应对页面改版。
    仅返回与当前视频 URL 匹配的 aweme，避免取到推荐/相关视频的数据。
    """
    aweme_id = _extract_aweme_id(video_url) or _extract_aweme_id(page.get_current_url() or "")
    result = page.evaluate(
        """
        (() => {
            const targetId = """ + json.dumps(aweme_id) + """;
            function extractFromAweme(sub) {
                if (!sub || !sub.aweme_id) return null;
                const stats = sub.statistics || {};
                const author = sub.author || {};
                const v = sub.video || {};
                const duration = v.duration ?? 0;
                const awemeType = sub.aweme_type || 0;
                let playUrl = '';
                const pa = v.play_addr || {};
                if (pa.url_list && pa.url_list.length) playUrl = pa.url_list[0];
                if (!playUrl && v.bit_rate && v.bit_rate.length) {
                    const br = v.bit_rate[0];
                    if (br?.play_addr?.url_list?.length) playUrl = br.play_addr.url_list[0];
                }
                if (!playUrl && v.download_addr?.url_list?.length)
                    playUrl = v.download_addr.url_list[0];

                // 笔记图片提取
                const images = [];
                if (awemeType === 68) {
                    const imagePostInfo = sub.image_post_info || {};
                    const rawImages = imagePostInfo.images || [];
                    for (const img of rawImages) {
                        const displayImage = (img || {}).display_image || {};
                        const urlList = displayImage.url_list || [];
                        if (urlList.length > 0) images.push(urlList[0]);
                    }
                }

                const urlPath = awemeType === 68 ? 'note' : 'video';
                const result = {
                    video_id: String(sub.aweme_id),
                    title: sub.desc || '',
                    author: author.nickname || '',
                    author_id: author.unique_id || author.short_id || '',
                    author_sec_uid: author.sec_uid || '',
                    author_signature: author.signature || '',
                    like_count: stats.digg_count || 0,
                    comment_count: stats.comment_count || 0,
                    share_count: stats.share_count || 0,
                    collect_count: stats.collect_count || 0,
                    cover_url: v.cover?.url_list?.[0] || '',
                    create_time: sub.create_time || 0,
                    duration: duration,
                    play_url: playUrl || null,
                    aweme_type: awemeType,
                    url: 'https://www.douyin.com/' + urlPath + '/' + String(sub.aweme_id),
                };
                if (images.length > 0) result.images = images;
                return result;
            }

            function findAweme(obj, depth) {
                if (depth > 20) return null;
                if (!obj || typeof obj !== 'object') return null;
                if (obj.aweme_id) {
                    const id = String(obj.aweme_id);
                    if (!targetId || id === targetId) return extractFromAweme(obj);
                }
                if (Array.isArray(obj)) {
                    for (const item of obj) {
                        const found = findAweme(item, depth + 1);
                        if (found) return found;
                    }
                    return null;
                }
                for (const k of Object.keys(obj)) {
                    const found = findAweme(obj[k], depth + 1);
                    if (found) return found;
                }
                return null;
            }

            // 1. RENDER_DATA - 支持多种嵌套结构
            const script = document.querySelector('script#RENDER_DATA');
            if (script) {
                try {
                    const decoded = decodeURIComponent(script.textContent);
                    const data = JSON.parse(decoded);
                    const found = findAweme(data, 0);
                    if (found) return JSON.stringify(found);
                } catch(e) {}
            }

            // 2. __ROUTER_DATA__ / loaderData (videoInfoRes.item_list) - 优先取当前视频
            if (window.__ROUTER_DATA__) {
                try {
                    const loaderData = window.__ROUTER_DATA__.loaderData || {};
                    for (const key of Object.keys(loaderData)) {
                        if (key.includes('video') || key.includes('note') || key.includes('page')) {
                            const pageData = loaderData[key];
                            const videoInfo = pageData?.videoInfoRes || pageData?.videoInfo;
                            const items = videoInfo?.item_list || videoInfo?.item_list || videoInfo?.aweme_detail;
                            const arr = Array.isArray(items) ? items : (items ? [items] : []);
                            for (const item of arr) {
                                if (item?.aweme_id) {
                                    const id = String(item.aweme_id);
                                    if (!targetId || id === targetId) {
                                        const found = extractFromAweme(item);
                                        if (found) return JSON.stringify(found);
                                    }
                                }
                            }
                        }
                    }
                } catch(e) {}
            }

            // 3. __INITIAL_STATE__
            if (window.__INITIAL_STATE__) {
                try {
                    const found = findAweme(window.__INITIAL_STATE__, 0);
                    if (found) return JSON.stringify(found);
                } catch(e) {}
            }

            // 4. 遍历所有 script 标签，查找可能包含 aweme 的 JSON（需匹配 targetId）
            const scripts = document.querySelectorAll('script[id]');
            for (const s of scripts) {
                if (s.id === 'RENDER_DATA') continue;
                try {
                    const text = s.textContent || '';
                    if (text.length < 500 || text.length > 500000) continue;
                    if (text.includes('aweme_id') && text.includes('statistics')) {
                        const match = text.match(/"aweme_id"\\s*:\\s*"([^"]+)"[^}]*"statistics"\\s*:\\s*\\{[^}]*"digg_count"\\s*:\\s*(\\d+)[^}]*"comment_count"\\s*:\\s*(\\d+)/);
                        if (match && (!targetId || match[1] === targetId)) {
                            return JSON.stringify({
                                video_id: match[1],
                                title: '',
                                author: '',
                                author_id: '',
                                author_sec_uid: '',
                                author_signature: '',
                                like_count: parseInt(match[2], 10) || 0,
                                comment_count: parseInt(match[3], 10) || 0,
                                share_count: 0,
                                collect_count: 0,
                                cover_url: '',
                                create_time: 0,
                                play_url: null,
                            });
                        }
                    }
                } catch(e) {}
            }
            return '';
        })()
        """
    )
    if result:
        try:
            info = json.loads(result)
            if not info.get("url") and video_url:
                info["url"] = video_url
            return info
        except json.JSONDecodeError:
            pass
    return None


# ========== 评论抓取 ==========
# 注：回复接口易触发反爬，后期模拟用户操作点击「查看更多」处理。

def fetch_comments(page: Page, video_url: str, max_comments: int = 30) -> list[dict[str, Any]]:
    """抓取视频评论。

    策略优先级（拦截优先，避免直接 fetch API 触发反爬验证）：
    1. 拦截（滚动触发浏览器自然请求，被动捕获响应）
    2. 源码正则
    3. DOM 解析
    4. 主动 fetch API（最后兜底，仅在前三种策略均不足时使用）
    """
    logger.info("抓取评论: %s", video_url)

    aweme_id = _extract_aweme_id(video_url)

    # 确保在视频详情页
    current_url = page.get_current_url()
    if aweme_id not in current_url:
        page.navigate(video_url)
        _wait_for_video_page(page, video_url)
        sleep_random(800, 1500)
        _wait_past_captcha(page)

    comments: list[dict[str, Any]] = []

    # 策略 1：拦截（滚动触发浏览器自然请求，被动捕获，反爬风险最低）
    if aweme_id:
        intercept_comments = _fetch_comments_via_intercept(page, aweme_id, max_comments)
        if intercept_comments:
            comments = intercept_comments
            logger.info("评论：拦截成功 %d 条", len(comments))

    # 策略 2：源码正则
    if len(comments) < max_comments // 2:
        source_comments = _extract_comments_from_source(page, max_comments)
        if source_comments:
            existing = {c["content"] for c in comments}
            for sc in source_comments:
                if sc["content"] not in existing:
                    comments.append(sc)
                    existing.add(sc["content"])

    # 策略 3：DOM 解析
    if len(comments) < max_comments // 2:
        for _ in range(20):
            page.scroll_by(0, 600)
            sleep_random(600, 1000)
        dom_comments = _extract_comments_from_dom(page, max_comments)
        if dom_comments:
            existing = {c["content"] for c in comments}
            for dc in dom_comments:
                if dc["content"] not in existing:
                    comments.append(dc)
                    existing.add(dc["content"])

    # 策略 4：主动 fetch API（最后兜底，直接 fetch 有反爬风险，仅在前三种策略均不足时使用）
    if len(comments) < max_comments // 2 and aweme_id:
        api_comments = _fetch_comments_via_api(page, aweme_id, max_comments)
        if api_comments:
            existing = {c["content"] for c in comments}
            for ac in api_comments:
                if ac["content"] not in existing:
                    comments.append(ac)
                    existing.add(ac["content"])
            logger.info("评论：API 兜底补充后共 %d 条", len(comments))

    comments.sort(key=lambda c: c.get("like_count", 0), reverse=True)
    result = comments[:max_comments]
    logger.info("获取 %d 条评论", len(result))
    return result


def _fetch_comments_via_intercept(
    page: Page, aweme_id: str, max_comments: int
) -> list[dict[str, Any]]:
    """通过拦截 comment/list 响应获取评论（滚动触发，多页累积）。"""
    if not aweme_id:
        return []

    with NetworkCapture(
        page, COMMENT_API_PATTERN, timeout=12, multi=True
    ) as capture:
        # 滚动触发评论加载，每轮滚动后轮询收集响应（优化：6 轮、1.5s 轮询，减少 xxClaw 超时）
        for round_idx in range(6):
            if capture.get_captured_count() > 0 and round_idx > 0:
                estimated = capture.get_captured_count() * 20
                if estimated >= max_comments:
                    break
            page.scroll_by(0, 600)
            sleep_random(500, 900)
            capture.poll_for(1.5)

        captured = capture.wait_for_capture_multi(min_count=0)

    return _extract_comments_from_intercepted(captured, max_comments)


def _extract_comments_from_intercepted(
    captured: list[tuple[dict[str, Any], dict[str, Any]]],
    max_comments: int,
) -> list[dict[str, Any]]:
    """从拦截的 comment/list 响应中提取评论。"""
    comments: list[dict[str, Any]] = []
    seen_content: set[str] = set()

    for _request, response in captured:
        body = response.get("body")
        if not body:
            continue
        try:
            data = _parse_douyin_response(body)
        except (ValueError, json.JSONDecodeError):
            continue
        if data.get("status_code", -1) != 0:
            continue
        raw_list = data.get("comments", [])
        for raw in raw_list:
            if len(comments) >= max_comments:
                break
            text = raw.get("text", "").strip()
            if not text or text in seen_content:
                continue
            seen_content.add(text)
            user_info = raw.get("user", {})
            comments.append({
                "comment_id": str(raw.get("cid", "")),
                "author": user_info.get("nickname", "未知用户"),
                "content": _traditional_to_simplified(text),
                "like_count": raw.get("digg_count", 0),
                "reply_count": raw.get("reply_comment_total", 0),
                "create_time": raw.get("create_time", 0),
            })

    return comments


def _fetch_comments_via_api(
    page: Page,
    aweme_id: str,
    max_comments: int = 30,
) -> list[dict[str, Any]]:
    """通过抖音评论 API 获取评论。"""
    comments: list[dict[str, Any]] = []
    cursor = 0
    page_count = 20

    for _ in range(10):
        if len(comments) >= max_comments:
            break

        api_url = (
            f"https://www.douyin.com/aweme/v1/web/comment/list/"
            f"?aweme_id={aweme_id}&cursor={cursor}&count={page_count}"
            f"&item_type=0&insert_ids=&whale_cut_token=&cut_version=1"
            f"&rcFT=&update_version_code=170400"
        )

        current_page_url = page.get_current_url() or ""
        if "/note/" in current_page_url:
            referer_url = f"https://www.douyin.com/note/{aweme_id}"
        else:
            referer_url = f"https://www.douyin.com/video/{aweme_id}"
        fetch_js = f"""
        (() => {{
            return new Promise((resolve) => {{
                fetch({json.dumps(api_url)}, {{
                    method: "GET",
                    credentials: "include",
                    headers: {{
                        "Accept": "application/json",
                        "Referer": "{referer_url}"
                    }}
                }})
                .then(r => r.text())
                .then(resolve)
                .catch(e => resolve(JSON.stringify({{"error": e.message}})));
            }});
        }})()
        """

        try:
            response_text = page.evaluate_async(fetch_js, timeout=15.0)
            if not response_text:
                break

            data = json.loads(response_text)
            if data.get("error") or data.get("status_code", -1) != 0:
                break

            raw_comments = data.get("comments", [])
            if not raw_comments:
                break

            for raw in raw_comments:
                text = raw.get("text", "").strip()
                if not text:
                    continue
                user_info = raw.get("user", {})
                comments.append({
                    "comment_id": str(raw.get("cid", "")),
                    "author": user_info.get("nickname", "未知用户"),
                    "content": _traditional_to_simplified(text),
                    "like_count": raw.get("digg_count", 0),
                    "reply_count": raw.get("reply_comment_total", 0),
                    "create_time": raw.get("create_time", 0),
                })

            if not data.get("has_more", 0):
                break
            cursor = data.get("cursor", cursor + page_count)
            time.sleep(1.5)
        except (json.JSONDecodeError, Exception) as e:
            logger.debug("API 评论抓取异常: %s", e)
            break

    logger.info("API 抓取到 %d 条评论", len(comments))
    return comments


def _extract_comments_from_source(
    page: Page, max_comments: int
) -> list[dict[str, Any]]:
    """从页面源码正则提取评论。"""
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()

    page_source = page.get_page_source()

    # 模式 1：text + digg_count
    pattern = re.compile(
        r'"text"\s*:\s*"([^"]{2,500})"[^}]{0,300}?"digg_count"\s*:\s*(\d+)',
        re.DOTALL,
    )
    for text, digg in pattern.findall(page_source):
        if len(comments) >= max_comments:
            break
        decoded = text.encode().decode("unicode_escape", errors="ignore").strip()
        if decoded and len(decoded) >= 2 and decoded not in seen:
            seen.add(decoded)
            comments.append({
                "comment_id": "",
                "author": "",
                "content": _traditional_to_simplified(decoded),
                "like_count": int(digg),
                "reply_count": 0,
                "create_time": 0,
            })

    # 模式 2：digg_count + text（反序）
    if len(comments) < max_comments // 2:
        pattern2 = re.compile(
            r'"digg_count"\s*:\s*(\d+)[^}]{0,300}?"text"\s*:\s*"([^"]{2,500})"',
            re.DOTALL,
        )
        for digg, text in pattern2.findall(page_source):
            if len(comments) >= max_comments:
                break
            decoded = text.encode().decode("unicode_escape", errors="ignore").strip()
            if decoded and len(decoded) >= 2 and decoded not in seen:
                seen.add(decoded)
                comments.append({
                    "comment_id": "",
                    "author": "",
                    "content": _traditional_to_simplified(decoded),
                    "like_count": int(digg),
                    "reply_count": 0,
                    "create_time": 0,
                })

    logger.info("源码正则提取到 %d 条评论", len(comments))
    return comments


def _extract_comments_from_dom(page: Page, max_comments: int) -> list[dict[str, Any]]:
    """从 DOM 提取评论。"""
    result = page.evaluate(
        f"""
        (() => {{
            const selectors = [
                "div[class*='comment-item-info-wrap']",
                "div[class*='commentItemInfoWrap']",
                "div[class*='comment-item-container']",
                "div[class*='commentItemContainer']",
            ];
            let containers = [];
            for (const sel of selectors) {{
                containers = document.querySelectorAll(sel);
                if (containers.length > 0) break;
            }}
            if (containers.length === 0) return '[]';

            const comments = [];
            for (let i = 0; i < Math.min(containers.length, {max_comments * 2}); i++) {{
                const container = containers[i];
                const spans = container.querySelectorAll('span');
                let content = '';
                for (const span of spans) {{
                    const t = span.textContent.trim();
                    if (!t) continue;
                    if (['分享','回复','举报','删除','展开','收起','作者'].includes(t)) continue;
                    if (/^\\d+[天周月年]前/.test(t) || /·/.test(t)) continue;
                    if (/^[\\d.]+[万千百]?$/.test(t)) continue;
                    if (t.length >= 2) {{
                        content = t;
                        break;
                    }}
                }}
                if (!content || content.length < 2) continue;

                let likeCount = 0;
                const fullText = container.textContent;
                const likeMatch = fullText.match(/(\\d+\\.?\\d*)[万w]/i);
                if (likeMatch) {{
                    likeCount = Math.round(parseFloat(likeMatch[1]) * 10000);
                }} else {{
                    const numMatch = fullText.match(/(\\d+)/);
                    if (numMatch) likeCount = parseInt(numMatch[1]);
                }}

                let replyCount = 0;
                const replyMatch = fullText.match(/(\\d+)\\s*条回复/);
                if (replyMatch) replyCount = parseInt(replyMatch[1]);

                comments.push({{
                    comment_id: '',
                    author: '未知用户',
                    content: content,
                    like_count: likeCount,
                    reply_count: replyCount,
                    create_time: 0,
                }});
            }}
            return JSON.stringify(comments);
        }})()
        """
    )

    try:
        comments = json.loads(result) if result else []
        logger.info("DOM 提取到 %d 条评论", len(comments))
        return comments
    except json.JSONDecodeError:
        return []


def _check_end_container(page: Page) -> bool:
    """检查是否到达评论底部。"""
    return page.has_element(COMMENT_END_CONTAINER)


def _get_comment_count(page: Page) -> int:
    """获取当前页面评论容器数量。"""
    return page.get_elements_count(PARENT_COMMENT)
