"""Whisper 语音转录模块。

从视频提取音频 → 使用 OpenAI Whisper 进行语音识别。

依赖：
- ffmpeg（系统安装）
- openai-whisper（pip install openai-whisper）
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Whisper 模型缓存：避免每次调用都重新加载（加载耗时 5-15s）
_model_cache: dict[str, Any] = {}

# 默认输出目录
_DEFAULT_OUTPUT_DIR = Path.home() / ".dingclaw" / "store-douyin" / "transcripts"


# ──────────────────────────────── 公共接口 ────────────────────────────────


def process_video(
    video_path: str | Path,
    output_dir: str | Path | None = None,
    whisper_model: str = "tiny",
) -> dict[str, Any]:
    """完整处理单个视频：提取音频 → Whisper 转录。

    Args:
        video_path: 视频文件路径。
        output_dir: 输出目录（默认使用视频所在目录）。
        whisper_model: Whisper 模型名称（tiny/base/small/medium/large）。

    Returns:
        {
            "success": bool,
            "transcript": str,
            "transcript_path": str | None,
            "segments": list[dict] | None,
            "model": str,
            "error": str | None,
        }
    """
    result: dict[str, Any] = {
        "success": False,
        "transcript": "",
        "transcript_path": None,
        "segments": None,
        "model": f"whisper-{whisper_model}",
        "error": None,
    }

    video_path = Path(video_path)
    if not video_path.exists():
        result["error"] = f"视频文件不存在: {video_path}"
        return result

    out_dir = Path(output_dir) if output_dir else video_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 提取音频
    audio_path = out_dir / f"{video_path.stem}_audio.wav"
    if not audio_path.exists():
        if not _extract_audio(video_path, audio_path):
            result["error"] = "音频提取失败（需要安装 ffmpeg）"
            return result
    else:
        logger.info("音频已存在: %s", audio_path)

    # Step 2: Whisper 转录
    whisper_result = transcribe_audio(str(audio_path), whisper_model)
    if whisper_result:
        transcript_text = whisper_result.get("text", "").strip()
        if not transcript_text:
            result["error"] = "转录结果为空（可能无语音、仅 BGM，或音频异常）"
            return result

        result["success"] = True
        result["transcript"] = transcript_text
        result["segments"] = whisper_result.get("segments")

        # 保存转录文本
        transcript_path = out_dir / f"{video_path.stem}_transcript.txt"
        transcript_path.write_text(result["transcript"], encoding="utf-8")
        result["transcript_path"] = str(transcript_path)

        logger.info(
            "转录完成（%s 模型），字数: %d",
            whisper_model,
            len(result["transcript"]),
        )
    else:
        result["error"] = "Whisper 转录失败"

    return result


def transcribe_audio(
    audio_path: str | Path,
    model_name: str = "tiny",
) -> dict[str, Any] | None:
    """使用 OpenAI Whisper 进行语音识别。

    优化点：
    - 模型缓存：同一 model_name 只加载一次，后续复用（省 5-15s）
    - 使用 whisper.load_audio() 加载音频（内部调 ffmpeg 重采样到 16kHz），
      无需额外依赖 soundfile/scipy

    Args:
        audio_path: 音频文件路径（WAV 16kHz 单声道最佳）。
        model_name: Whisper 模型名称。

    Returns:
        {"text": str, "segments": list[dict], "model": str} 或 None。
    """
    logger.info("使用 Whisper (%s) 转录: %s", model_name, audio_path)

    try:
        import whisper
    except ImportError as import_error:
        logger.error(
            "缺少依赖: %s。请运行: pip install openai-whisper",
            import_error,
        )
        return None

    try:
        # 模型缓存：避免每次调用都重新加载（首次 5-15s，后续 <0.01s）
        if model_name not in _model_cache:
            logger.info("首次加载 Whisper 模型: %s", model_name)
            _model_cache[model_name] = whisper.load_model(model_name)
        model = _model_cache[model_name]

        # whisper.load_audio() 内部调 ffmpeg 做重采样到 16kHz float32，
        # 无需手动 soundfile + scipy.signal.resample
        audio_data = whisper.load_audio(str(audio_path))
        whisper_result = model.transcribe(audio_data, language="zh", verbose=False)

        return {
            "text": whisper_result.get("text", ""),
            "segments": whisper_result.get("segments", []),
            "model": f"whisper-{model_name}",
        }

    except Exception as whisper_error:
        logger.error("Whisper 转录失败: %s", whisper_error)
        return None


def process_video_dir(
    video_dir: str | Path,
    whisper_model: str = "tiny",
) -> dict[str, Any]:
    """处理目录中的视频文件（自动查找视频）。

    Args:
        video_dir: 包含视频文件的目录。
        whisper_model: Whisper 模型名称。

    Returns:
        与 process_video() 相同的结果字典。
    """
    video_dir = Path(video_dir)
    result: dict[str, Any] = {
        "success": False,
        "transcript": "",
        "transcript_path": None,
        "segments": None,
        "model": f"whisper-{whisper_model}",
        "error": None,
    }

    # 查找视频文件
    video_path = _find_video_file(video_dir)
    if not video_path:
        result["error"] = f"目录中未找到视频文件: {video_dir}"
        return result

    return process_video(video_path, output_dir=video_dir, whisper_model=whisper_model)


# ──────────────────────────────── 内部方法 ────────────────────────────────


def _extract_audio(video_path: Path, output_path: Path) -> bool:
    """从视频中提取音频（WAV 16kHz 单声道）。

    优先使用 ffmpeg，降级使用 moviepy。
    """
    # 策略 1: ffmpeg
    if _extract_audio_ffmpeg(video_path, output_path):
        return True

    # 策略 2: moviepy
    if _extract_audio_moviepy(video_path, output_path):
        return True

    logger.error("音频提取失败，请安装 ffmpeg 或 moviepy")
    return False


def _extract_audio_ffmpeg(video_path: Path, output_path: Path) -> bool:
    """使用 ffmpeg 提取音频。优先使用 imageio_ffmpeg（跨平台），否则回退系统 ffmpeg。"""
    ffmpeg_exe = "ffmpeg"
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    cmd = [
        ffmpeg_exe, "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-y", str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if output_path.exists():
            logger.info("ffmpeg 音频提取完成: %s", output_path)
            return True
    except FileNotFoundError:
        logger.debug("ffmpeg 未安装")
    except subprocess.TimeoutExpired:
        logger.debug("ffmpeg 提取超时")
    except Exception as error:
        logger.debug("ffmpeg 提取失败: %s", error)
    return False


def _extract_audio_moviepy(video_path: Path, output_path: Path) -> bool:
    """使用 moviepy 提取音频（降级方案）。"""
    try:
        import os

        try:
            import imageio_ffmpeg
            os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            pass

        from moviepy import VideoFileClip

        video = VideoFileClip(str(video_path))
        video.audio.write_audiofile(
            str(output_path),
            fps=16000,
            nbytes=2,
            codec="pcm_s16le",
        )
        video.close()

        if output_path.exists():
            logger.info("moviepy 音频提取完成: %s", output_path)
            return True
    except ImportError:
        logger.debug("moviepy 未安装")
    except Exception as moviepy_error:
        logger.debug("moviepy 提取失败: %s", moviepy_error)
    return False


def _find_video_file(directory: Path) -> Path | None:
    """在目录中查找视频文件。"""
    video_extensions = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv"}
    for ext in video_extensions:
        # 优先查找 video.xxx
        candidate = directory / f"video{ext}"
        if candidate.exists():
            return candidate

    # 查找任意视频文件
    for file_path in directory.iterdir():
        if file_path.is_file() and file_path.suffix.lower() in video_extensions:
            return file_path

    return None
