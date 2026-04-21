"""字幕提取模块。

两级策略（抖音 web 端无原生字幕接口，yt-dlp/RENDER_DATA 均无效）：
0. 若传入 subtitle_infos（来自 aweme 拦截），直接下载解析
1. 必剪云端 ASR 转录（bcut_asr）

支持 SRT / VTT 格式解析为纯文本。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

from .bcut_asr import extract_audio_with_ffmpeg, transcribe_with_bcut
from .cdp import Page

logger = logging.getLogger(__name__)


# 默认输出目录
_DEFAULT_OUTPUT_DIR = Path.home() / ".dingclaw" / "store-douyin" / "subtitles"


# ──────────────────────────────── 公共接口 ────────────────────────────────


def extract_subtitle(
    page: Page,
    video_url: str,
    output_dir: str | None = None,
    cookie_string: str = "",
    subtitle_infos: list[dict] | None = None,
    play_url: str | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """提取视频字幕（两级策略）。

    降级链路：
    0. 若传入 subtitle_infos（来自拦截的 aweme 响应），直接下载解析
    1. 必剪云端 ASR 转录（抖音 web 无原生字幕，yt-dlp/RENDER_DATA 均无效）

    Args:
        page: CDP 页面对象。
        video_url: 视频 URL。
        output_dir: 输出目录。
        cookie_string: Cookie 字符串（用于 yt-dlp 下载音频）。
        subtitle_infos: 可选的预提取字幕信息（来自 aweme API 拦截）。
        play_url: 可选的视频直链（来自 aweme 拦截），ASR 降级时用于下载。
        duration_ms: 视频时长（毫秒），超过 60s 时跳过必剪 ASR。

    Returns:
        {
            "success": bool,
            "subtitle_text": str,
            "subtitle_path": str | None,
            "subtitle_type": "ssr" | "bcut_asr" | None,
            "error": str | None,
        }
    """
    result: dict[str, Any] = {
        "success": False,
        "subtitle_text": "",
        "subtitle_path": None,
        "subtitle_type": None,
        "error": None,
    }

    out_dir = Path(output_dir) if output_dir else _DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # 策略 0：使用预提取的 subtitle_infos（来自拦截的 aweme 响应）
    if subtitle_infos and len(subtitle_infos) > 0:
        all_texts = _download_and_parse_subtitle_infos(subtitle_infos)
        if all_texts:
            combined = "\n".join(all_texts)
            result["success"] = True
            result["subtitle_text"] = combined
            result["subtitle_type"] = "ssr"
            aweme_id = _extract_aweme_id(video_url)
            subtitle_file = out_dir / f"{aweme_id or 'video'}_ssr.txt"
            subtitle_file.write_text(combined, encoding="utf-8")
            result["subtitle_path"] = str(subtitle_file)
            logger.info("从拦截响应提取到字幕，字数: %d", len(combined))
            return result

    # 策略 1：必剪云端 ASR 转录（支持 play_url 直链兜底）
    # 超过 60s 跳过，避免长视频必剪 ASR 耗时过长
    dur_sec = (duration_ms or 0) / 1000.0 if (duration_ms or 0) > 1000 else (duration_ms or 0)
    if dur_sec > 60:
        result["error"] = "视频时长超过 60s，跳过必剪 ASR"
        logger.info("视频时长 %.1fs > 60s，跳过必剪 ASR", dur_sec)
        return result

    bcut_result = _extract_with_bcut_asr(
        video_url, out_dir, cookie_string, play_url=play_url
    )
    if bcut_result["success"]:
        return bcut_result

    result["error"] = "字幕提取失败"
    logger.warning(
        "字幕提取失败: %s | 必剪 ASR: %s",
        video_url,
        bcut_result.get("error") or "未知",
    )
    return result


# ──────────────────────────────── 字幕解析 ────────────────────────────────


def parse_srt_content(srt_content: str) -> str:
    """解析 SRT 格式字幕内容为纯文本。"""
    seen_lines: set[str] = set()
    lines: list[str] = []

    # 移除序号行和时间戳行
    cleaned = re.sub(r"^\d+\s*$", "", srt_content, flags=re.MULTILINE)
    cleaned = re.sub(
        r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}.*\n",
        "\n",
        cleaned,
    )
    # 移除 HTML 标签
    cleaned = re.sub(r"<[^>]+>", "", cleaned)

    for line in cleaned.split("\n"):
        line = line.strip()
        if line and line not in seen_lines:
            seen_lines.add(line)
            lines.append(line)

    return "\n".join(lines)


def parse_vtt_content(vtt_content: str) -> str:
    """解析 VTT 格式字幕内容为纯文本。"""
    seen_lines: set[str] = set()
    lines: list[str] = []

    # 移除 WEBVTT 头部
    cleaned = re.sub(r"WEBVTT.*?\n\n", "", vtt_content, flags=re.DOTALL)
    # 移除时间戳行
    cleaned = re.sub(
        r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*\n",
        "\n",
        cleaned,
    )
    # 移除 HTML 标签
    cleaned = re.sub(r"<[^>]+>", "", cleaned)

    for line in cleaned.split("\n"):
        line = line.strip()
        if not line or line.isdigit():
            continue
        if line not in seen_lines:
            seen_lines.add(line)
            lines.append(line)

    return "\n".join(lines)


# ──────────────────────────────── 内部方法 ────────────────────────────────


def _build_ytdlp_cookie_args(cookie_string: str) -> tuple[list[str], str | None]:
    """构建 yt-dlp 的 Cookie 参数。

    Returns:
        (cookie_args, cookie_file_path)
        cookie_file_path 不为 None 时需要在使用后清理。
    """
    if not cookie_string or not cookie_string.strip():
        return [], None

    # 写入 Netscape 格式 Cookie 文件
    cookie_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="ytdlp_cookie_"
    )
    cookie_file.write("# Netscape HTTP Cookie File\n")

    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            cookie_file.write(f".douyin.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")

    cookie_file.close()
    return ["--cookies", cookie_file.name], cookie_file.name


def _download_and_parse_subtitle_infos(subtitle_infos: list[dict]) -> list[str]:
    """下载并解析 subtitle_infos 中的字幕内容。"""
    all_texts: list[str] = []

    for sub_info in subtitle_infos:
        if not isinstance(sub_info, dict):
            continue

        sub_url = sub_info.get("url", "")
        sub_format = sub_info.get("format", "srt")

        if not sub_url:
            # 尝试 url_list
            url_list = sub_info.get("url_list", [])
            if url_list and isinstance(url_list, list):
                sub_url = url_list[0]

        if not sub_url:
            continue

        try:
            req = urllib.request.Request(
                sub_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.douyin.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                sub_content = response.read().decode("utf-8", errors="ignore")

                if sub_format == "vtt":
                    text = parse_vtt_content(sub_content)
                else:
                    text = parse_srt_content(sub_content)

                if text.strip():
                    all_texts.append(text)
        except Exception as download_error:
            logger.debug("字幕下载失败: %s", download_error)
            continue

    return all_texts


def _extract_with_bcut_asr(
    video_url: str,
    out_dir: Path,
    cookie_string: str,
    play_url: str | None = None,
) -> dict[str, Any]:
    """使用必剪云端 ASR 转录（最终降级方案）。

    流程：yt-dlp 下载音频 → 失败时用 play_url 直链下载视频 → 提取音频 → 必剪 ASR。
    """
    result: dict[str, Any] = {
        "success": False,
        "subtitle_text": "",
        "subtitle_path": None,
        "subtitle_type": "bcut_asr",
        "error": None,
    }

    audio_dir = out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    cookie_args, cookie_file_path = _build_ytdlp_cookie_args(cookie_string)
    audio_path: Path | None = None

    try:
        # 优化：有 play_url 时优先直链下载（抖音 web 端 yt-dlp 音频常失败，可省 1～2s）
        if play_url and play_url.strip():
            try:
                video_path = audio_dir / "video.mp4"
                req = urllib.request.Request(
                    play_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://www.douyin.com/",
                    },
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    video_path.write_bytes(resp.read())
                if video_path.exists() and video_path.stat().st_size > 0:
                    logger.info("play_url 直链下载成功（优先）: %s", video_path.name)
                    audio_path = audio_dir / "audio_extracted.wav"
                    if extract_audio_with_ffmpeg(str(video_path), str(audio_path)):
                        pass  # audio_path 已设置
                    else:
                        audio_path = None
            except Exception as e:
                logger.debug("play_url 优先下载失败: %s，降级 yt-dlp", e)

        # 无 play_url 或直链失败时，用 yt-dlp 下载音频
        if not audio_path or not audio_path.exists():
            logger.info("下载音频用于 ASR 转录...")
            audio_cmd = [
                "yt-dlp",
                "-f", "bestaudio/best",
                "-x", "--audio-format", "wav",
                "--audio-quality", "0",
                "--postprocessor-args", "-ar 16000 -ac 1",
                "-o", str(audio_dir / "audio.%(ext)s"),
                *cookie_args,
                video_url,
            ]
            subprocess.run(
                audio_cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            for ext in (".wav", ".mp3", ".m4a", ".aac"):
                candidate = audio_dir / f"audio{ext}"
                if candidate.exists():
                    audio_path = candidate
                    break

        # 如果 yt-dlp 音频下载失败，尝试下载视频再提取音频（play_url 或 yt-dlp 视频）
        if not audio_path or not audio_path.exists():
            logger.info("yt-dlp 音频下载失败，尝试下载视频再提取音频...")
            video_path: Path | None = None

            # 优先使用 play_url 直链（来自 aweme 拦截，成功率更高）
            if play_url and play_url.strip():
                try:
                    video_path = audio_dir / "video.mp4"
                    req = urllib.request.Request(
                        play_url,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/131.0.0.0 Safari/537.36"
                            ),
                            "Referer": "https://www.douyin.com/",
                        },
                    )
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        video_path.write_bytes(resp.read())
                    if video_path.exists() and video_path.stat().st_size > 0:
                        logger.info("play_url 直链下载成功: %s", video_path.name)
                except Exception as e:
                    logger.info("play_url 直链下载失败: %s", e)
                    video_path = None

            # 兜底：yt-dlp 下载视频
            if not video_path or not video_path.exists():
                video_cmd = [
                    "yt-dlp",
                    "-f", "best[height<=720]/best",
                    "-o", str(audio_dir / "video.%(ext)s"),
                    *cookie_args,
                    video_url,
                ]
                subprocess.run(
                    video_cmd,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                for ext in (".mp4", ".webm", ".mkv"):
                    candidate = audio_dir / f"video{ext}"
                    if candidate.exists():
                        video_path = candidate
                        break

            if video_path and video_path.exists():
                audio_path = audio_dir / "audio_extracted.wav"
                if not extract_audio_with_ffmpeg(str(video_path), str(audio_path)):
                    audio_path = None

        if not audio_path or not audio_path.exists():
            result["error"] = "无法获取音频"
            logger.warning("音频获取失败")
            return result

        logger.info("音频准备完成: %s", audio_path.name)

        # Step 2: 必剪 ASR 转录
        logger.info("开始必剪云端 ASR 转录...")
        bcut_result = transcribe_with_bcut(str(audio_path), str(audio_dir))

        if bcut_result.get("success"):
            result["success"] = True
            result["subtitle_text"] = bcut_result.get("text", "")
            result["subtitle_path"] = bcut_result.get("output_file")
            logger.info("必剪 ASR 转录完成，字数: %d", len(result["subtitle_text"]))
        else:
            result["error"] = bcut_result.get("error", "必剪 ASR 转录失败")
            logger.warning("必剪 ASR 转录失败: %s", result["error"])

    except subprocess.TimeoutExpired:
        result["error"] = "音频下载超时"
        logger.warning("音频下载超时")
    except FileNotFoundError:
        result["error"] = "未安装 yt-dlp"
        logger.warning("yt-dlp 未安装")
    except Exception as unexpected_error:
        result["error"] = f"ASR 转录异常: {unexpected_error}"
        logger.warning("ASR 转录异常: %s", unexpected_error)
    finally:
        if cookie_file_path and os.path.exists(cookie_file_path):
            try:
                os.unlink(cookie_file_path)
            except OSError:
                pass

    return result


def _extract_aweme_id(video_url: str) -> str:
    """从视频 URL 中提取 aweme_id。"""
    match = re.search(r"/video/(\d+)", video_url)
    if match:
        return match.group(1)
    match = re.search(r"modal_id=(\d+)", video_url)
    if match:
        return match.group(1)
    return ""
