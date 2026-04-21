"""视频详情页：视频下载、字幕提取。

注意：评论抓取在 feed_detail.py 中。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .cdp import Page
from .human import sleep_random
from .urls import make_video_detail_url

logger = logging.getLogger(__name__)

# 默认输出目录
DEFAULT_OUTPUT_DIR = Path.home() / ".dingclaw" / "store-douyin" / "downloads"


def download_video(
    page: Page,
    video_url: str,
    output_dir: str | None = None,
    filename: str | None = None,
    play_url: str | None = None,
) -> str | None:
    """下载视频文件。

    优先使用 play_url 直链（无需 page），其次 yt-dlp，最后从页面提取。

    Args:
        page: CDP 页面对象。
        video_url: 视频页面 URL。
        output_dir: 输出目录。
        filename: 文件名（不含扩展名）。
        play_url: 可选的视频直链（来自 aweme 拦截），有则优先使用，不依赖 page。

    Returns:
        下载的文件路径，失败返回 None。
    """
    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    aweme_id = _extract_aweme_id(video_url)
    if not filename:
        filename = aweme_id or f"video_{int(time.time())}"

    # 策略 0：play_url 直链（无需 page，可并行）
    if play_url and play_url.strip():
        filepath = _download_from_play_url(play_url, out_dir, filename)
        if filepath:
            return filepath

    # 策略 1：yt-dlp
    filepath = _download_with_ytdlp(video_url, out_dir, filename)
    if filepath:
        return filepath

    # 策略 2：从页面提取视频源地址
    filepath = _download_from_page_source(page, video_url, out_dir, filename)
    if filepath:
        return filepath

    logger.warning("视频下载失败: %s", video_url)
    return None


def extract_subtitles(
    page: Page,
    video_url: str,
    output_dir: str | None = None,
    cookie_string: str = "",
    subtitle_infos: list[dict] | None = None,
    play_url: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """提取视频字幕（两级策略：API subtitle_infos → 必剪 ASR）。

    Args:
        page: CDP 页面对象。
        video_url: 视频页面 URL。
        output_dir: 输出目录。
        cookie_string: Cookie 字符串（用于 yt-dlp）。
        subtitle_infos: 可选的预提取字幕信息（来自 aweme API 拦截）。
        play_url: 可选的视频直链（来自 aweme 拦截），ASR 降级时用于下载。
        duration_ms: 视频时长（毫秒），超过 60s 时跳过必剪 ASR。

    Returns:
        {
            "success": bool,
            "subtitle_text": str,
            "subtitle_path": str | None,
            "subtitle_type": "manual" | "auto" | "ssr" | "bcut_asr" | None,
            "error": str | None,
        }
    """
    from .subtitle_extractor import extract_subtitle

    out_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    return extract_subtitle(
        page,
        video_url,
        str(out_dir),
        cookie_string,
        subtitle_infos,
        play_url=play_url,
        duration_ms=duration_ms,
    )


def get_video_source_url(page: Page, video_url: str) -> str | None:
    """从页面提取视频源地址（无水印）。

    Args:
        page: CDP 页面对象。
        video_url: 视频页面 URL。

    Returns:
        视频源 URL，失败返回 None。
    """
    current_url = page.get_current_url()
    aweme_id = _extract_aweme_id(video_url)
    if aweme_id and aweme_id not in current_url:
        page.navigate(video_url)
        page.wait_for_load()
        page.wait_dom_stable()
        sleep_random(800, 1500)

    # 从 SSR RENDER_DATA 提取（仅当前视频，targetId 避免取到推荐视频）
    target_id = json.dumps(aweme_id) if aweme_id else '""'
    src = page.evaluate(
        """
        (() => {
            const targetId = """ + target_id + """;
            const script = document.querySelector('script#RENDER_DATA');
            if (!script) return '';
            try {
                const decoded = decodeURIComponent(script.textContent);
                const data = JSON.parse(decoded);
                function findPlayUrl(obj, depth) {
                    if (depth > 15 || !obj || typeof obj !== 'object') return '';
                    if (obj.aweme_id && obj.video) {
                        const id = String(obj.aweme_id);
                        if (!targetId || id === targetId) {
                            const v = obj.video;
                            if (v.play_addr?.url_list?.length) return v.play_addr.url_list[0];
                            if (v.bit_rate?.length && v.bit_rate[0]?.play_addr?.url_list?.length)
                                return v.bit_rate[0].play_addr.url_list[0];
                        }
                    }
                    if (Array.isArray(obj)) {
                        for (const item of obj) {
                            const u = findPlayUrl(item, depth + 1);
                            if (u) return u;
                        }
                        return '';
                    }
                    for (const k of Object.keys(obj)) {
                        const u = findPlayUrl(obj[k], depth + 1);
                        if (u) return u;
                    }
                    return '';
                }
                return findPlayUrl(data, 0);
            } catch(e) {}
            return '';
        })()
        """
    )
    if src:
        return src

    # 从 video 标签提取
    src = page.evaluate(
        """
        (() => {
            const video = document.querySelector('video');
            if (video) {
                return video.src || video.querySelector('source')?.src || '';
            }
            return '';
        })()
        """
    )
    return src if src else None


# ========== 内部方法 ==========


def _extract_aweme_id(video_url: str) -> str:
    """从视频 URL 中提取 aweme_id。"""
    m = re.search(r"/video/(\d+)", video_url)
    if m:
        return m.group(1)
    m = re.search(r"modal_id=(\d+)", video_url)
    if m:
        return m.group(1)
    return ""


def _download_with_ytdlp(
    video_url: str, out_dir: Path, filename: str
) -> str | None:
    """使用 yt-dlp 下载视频。"""
    output_template = str(out_dir / f"{filename}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--quiet",
        "-o", output_template,
        video_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            for ext in ("mp4", "webm", "mkv", "flv"):
                filepath = out_dir / f"{filename}.{ext}"
                if filepath.exists():
                    logger.info("yt-dlp 下载成功: %s", filepath)
                    return str(filepath)
        else:
            logger.debug("yt-dlp 下载失败: %s", result.stderr[:200])
    except FileNotFoundError:
        logger.debug("yt-dlp 未安装")
    except subprocess.TimeoutExpired:
        logger.debug("yt-dlp 下载超时")

    return None


def _download_from_play_url(
    play_url: str, out_dir: Path, filename: str
) -> str | None:
    """从 play_url 直链下载视频（无需 page）。"""
    filepath = out_dir / f"{filename}.mp4"
    try:
        import requests

        resp = requests.get(
            play_url,
            headers={
                "Referer": "https://www.douyin.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            stream=True,
            timeout=60,
        )
        resp.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info("play_url 直链下载成功: %s", filepath)
        return str(filepath)
    except Exception as e:
        logger.debug("play_url 直链下载失败: %s", e)
        return None


def _download_from_page_source(
    page: Page, video_url: str, out_dir: Path, filename: str
) -> str | None:
    """从页面源地址下载视频。"""
    src = get_video_source_url(page, video_url)
    if not src:
        return None

    filepath = out_dir / f"{filename}.mp4"
    try:
        import requests

        resp = requests.get(
            src,
            headers={
                "Referer": "https://www.douyin.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            stream=True,
            timeout=60,
        )
        resp.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info("页面源地址下载成功: %s", filepath)
        return str(filepath)
    except Exception as e:
        logger.debug("页面源地址下载失败: %s", e)
        return None


