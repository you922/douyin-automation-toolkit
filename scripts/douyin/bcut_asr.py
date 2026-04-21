"""必剪云端语音识别（B站免费 ASR 服务）。

作为字幕提取的最终降级方案：当 yt-dlp 和 SSR 字幕都无法获取时，
上传音频到必剪云端进行语音识别，返回带时间戳的 SRT 格式字幕。

优势：
- 免费：使用 B站/必剪的免费 ASR 服务
- 准确：针对中文优化，识别准确率高
- 快速：云端处理，无需本地 GPU
- 完整：返回带时间戳的 SRT 格式字幕
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ──────────────────────────────── API 端点 ────────────────────────────────

_API_REQ_UPLOAD = "https://member.bilibili.com/x/bcut/rubick-interface/resource/create"
_API_COMMIT_UPLOAD = "https://member.bilibili.com/x/bcut/rubick-interface/resource/create/complete"
_API_CREATE_TASK = "https://member.bilibili.com/x/bcut/rubick-interface/task"
_API_QUERY_RESULT = "https://member.bilibili.com/x/bcut/rubick-interface/task/result"

_SUPPORTED_FORMATS = {"flac", "aac", "m4a", "mp3", "wav"}


# ──────────────────────────────── 数据模型 ────────────────────────────────


class _ResultState(Enum):
    STOP = 0
    RUNNING = 1
    ERROR = 3
    COMPLETE = 4


class ASRSegment:
    """单条语音识别断句。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.start_time: int = data.get("start_time", 0)
        self.end_time: int = data.get("end_time", 0)
        self.transcript: str = data.get("transcript", "")
        self.confidence: float = data.get("confidence", 0.0)

    def to_srt_timestamp(self) -> str:
        """转换为 SRT 时间戳格式。"""

        def _fmt(ms: int) -> str:
            hours = ms // 3_600_000
            minutes = (ms // 60_000) % 60
            seconds = (ms // 1_000) % 60
            millis = ms % 1_000
            return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

        return f"{_fmt(self.start_time)} --> {_fmt(self.end_time)}"


class ASRResult:
    """语音识别完整结果。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.utterances = [ASRSegment(u) for u in data.get("utterances", [])]
        self.version: str = data.get("version", "")

    @property
    def has_data(self) -> bool:
        return len(self.utterances) > 0

    def to_text(self) -> str:
        """纯文本（无时间标记）。"""
        return "\n".join(seg.transcript for seg in self.utterances)

    def to_srt(self) -> str:
        """SRT 字幕格式。"""
        return "\n".join(
            f"{idx}\n{seg.to_srt_timestamp()}\n{seg.transcript}\n"
            for idx, seg in enumerate(self.utterances, 1)
        )


# ──────────────────────────────── 异常 ────────────────────────────────


class BcutAPIError(Exception):
    """必剪 API 错误。"""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"BcutAPI [{code}]: {message}")


# ──────────────────────────────── 核心类 ────────────────────────────────


class BcutASR:
    """必剪语音识别接口。

    用法::

        asr = BcutASR("audio.mp3")
        asr.upload()
        asr.create_task()
        result = asr.wait_for_result()
        print(result.to_text())
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        })
        self._task_id: str | None = None
        self._etags: list[str] = []
        self._sound_bin: bytes | None = None
        self._sound_fmt: str | None = None
        self._sound_name: str | None = None

        # 上传后填充
        self._in_boss_key: str = ""
        self._resource_id: str = ""
        self._upload_id: str = ""
        self._upload_urls: list[str] = []
        self._per_size: int = 0
        self._download_url: str = ""

        if file_path:
            self.set_data(file_path)

    def set_data(
        self,
        file_path: str | Path | None = None,
        raw_data: bytes | None = None,
        data_fmt: str | None = None,
    ) -> None:
        """设置音频数据。"""
        if file_path:
            file_path = Path(file_path)
            self._sound_bin = file_path.read_bytes()
            self._sound_fmt = data_fmt or file_path.suffix.lstrip(".").lower()
            self._sound_name = file_path.name
        elif raw_data:
            if not data_fmt:
                raise ValueError("raw_data 模式必须提供 data_fmt")
            self._sound_bin = raw_data
            self._sound_fmt = data_fmt
            self._sound_name = f"{int(time.time())}.{data_fmt}"
        else:
            raise ValueError("必须提供 file_path 或 raw_data")

        logger.info("加载音频: %s (%dKB)", self._sound_name, len(self._sound_bin) // 1024)

    def upload(self) -> None:
        """上传音频文件到必剪服务器。"""
        if not self._sound_bin or not self._sound_fmt:
            raise ValueError("未设置音频数据，请先调用 set_data()")

        # 申请上传
        resp = self._session.post(_API_REQ_UPLOAD, data={
            "type": 2,
            "name": self._sound_name,
            "size": len(self._sound_bin),
            "resource_file_type": self._sound_fmt,
            "model_id": 7,
        })
        resp.raise_for_status()
        resp_json = resp.json()

        if resp_json.get("code"):
            raise BcutAPIError(resp_json["code"], resp_json.get("message", "未知错误"))

        data = resp_json["data"]
        self._in_boss_key = data["in_boss_key"]
        self._resource_id = data["resource_id"]
        self._upload_id = data["upload_id"]
        self._upload_urls = data["upload_urls"]
        self._per_size = data["per_size"]

        clip_count = len(self._upload_urls)
        logger.info("申请上传成功，共 %d 个分片", clip_count)

        # 分片上传
        self._etags = []
        for clip_index in range(clip_count):
            start = clip_index * self._per_size
            end = (clip_index + 1) * self._per_size
            resp = self._session.put(
                self._upload_urls[clip_index],
                data=self._sound_bin[start:end],
            )
            resp.raise_for_status()
            self._etags.append(resp.headers.get("Etag", ""))

        # 完成上传
        resp = self._session.post(_API_COMMIT_UPLOAD, data={
            "in_boss_key": self._in_boss_key,
            "resource_id": self._resource_id,
            "etags": ",".join(self._etags),
            "upload_id": self._upload_id,
            "model_id": 7,
        })
        resp.raise_for_status()
        resp_json = resp.json()

        if resp_json.get("code"):
            raise BcutAPIError(resp_json["code"], resp_json.get("message", "未知错误"))

        self._download_url = resp_json["data"]["download_url"]
        logger.info("音频上传完成")

    def create_task(self, max_retries: int = 3) -> str:
        """创建识别任务。"""
        for attempt in range(1, max_retries + 1):
            resp = self._session.post(_API_CREATE_TASK, json={
                "resource": self._download_url,
                "model_id": "7",
            })
            resp.raise_for_status()
            resp_json = resp.json()

            if resp_json.get("code") == 0:
                self._task_id = resp_json["data"]["task_id"]
                logger.info("语音识别任务已创建: %s", self._task_id[:8])
                return self._task_id

            if resp_json.get("code") == -504 and attempt < max_retries:
                logger.warning("创建任务超时，第 %d/%d 次重试...", attempt, max_retries)
                time.sleep(2 * attempt)
                continue

            raise BcutAPIError(resp_json["code"], resp_json.get("message", "未知错误"))

        raise BcutAPIError(-1, "创建任务失败，已达最大重试次数")

    def wait_for_result(self, timeout: int = 300) -> ASRResult:
        """等待识别完成并获取结果。"""
        if not self._task_id:
            raise ValueError("未创建任务，请先调用 create_task()")

        logger.info("等待语音识别完成...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            resp = self._session.get(_API_QUERY_RESULT, params={
                "model_id": 7,
                "task_id": self._task_id,
            })
            resp.raise_for_status()
            resp_json = resp.json()

            if resp_json.get("code"):
                raise BcutAPIError(resp_json["code"], resp_json.get("message", "未知错误"))

            result_data = resp_json["data"]
            state = result_data.get("state")

            if state == _ResultState.COMPLETE.value:
                logger.info("语音识别完成")
                return ASRResult(json.loads(result_data.get("result", "{}")))

            if state == _ResultState.ERROR.value:
                raise BcutAPIError(-1, f"识别失败: {result_data.get('remark', '未知错误')}")

            time.sleep(1)

        raise BcutAPIError(-1, f"识别超时（{timeout}秒）")


# ──────────────────────────────── 便捷函数 ────────────────────────────────


def extract_audio_with_ffmpeg(video_path: str, output_audio_path: str) -> bool:
    """使用 ffmpeg 从视频提取音频（WAV 16kHz 单声道）。

    优先使用 imageio-ffmpeg 内置二进制（pyproject.toml 已声明），无则用系统 ffmpeg。

    Args:
        video_path: 视频文件路径。
        output_audio_path: 输出音频路径（建议 .wav）。

    Returns:
        是否成功。
    """
    ffmpeg_exe = "ffmpeg"
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    cmd = [
        ffmpeg_exe, "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "16000", "-ac", "1",
        "-y", output_audio_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if Path(output_audio_path).exists():
            logger.info("ffmpeg 音频提取成功: %s", output_audio_path)
            return True
    except FileNotFoundError:
        logger.debug("ffmpeg 未安装")
    except subprocess.TimeoutExpired:
        logger.debug("ffmpeg 提取超时")
    except Exception as error:
        logger.debug("ffmpeg 提取失败: %s", error)
    return False


def transcribe_with_bcut(
    audio_path: str,
    output_dir: str | None = None,
    output_format: str = "txt",
) -> dict[str, Any]:
    """使用必剪云端 ASR 转录音频文件。

    Args:
        audio_path: 音频文件路径（支持 aac/mp3/wav/flac/m4a）。
        output_dir: 输出目录（默认使用音频所在目录）。
        output_format: 输出格式（txt/srt/json）。

    Returns:
        {"success": bool, "text": str, "srt": str, "output_file": str|None, "error": str|None}
    """
    result: dict[str, Any] = {
        "success": False,
        "text": "",
        "srt": "",
        "output_file": None,
        "error": None,
    }

    audio_path_obj = Path(audio_path)
    if not audio_path_obj.exists():
        result["error"] = f"音频文件不存在: {audio_path}"
        return result

    out_dir = Path(output_dir) if output_dir else audio_path_obj.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        asr = BcutASR(str(audio_path_obj))
        asr.upload()
        asr.create_task()
        asr_data = asr.wait_for_result()

        if not asr_data.has_data:
            result["error"] = "未识别到语音内容"
            return result

        result["text"] = asr_data.to_text()
        result["srt"] = asr_data.to_srt()
        result["success"] = True

        # 保存到文件
        stem = audio_path_obj.stem
        if output_format == "srt":
            output_file = out_dir / f"{stem}_bcut.srt"
            output_file.write_text(result["srt"], encoding="utf-8")
        elif output_format == "json":
            output_file = out_dir / f"{stem}_bcut.json"
            output_file.write_text(
                json.dumps(
                    {
                        "text": result["text"],
                        "srt": result["srt"],
                        "segments": [
                            {
                                "start": seg.start_time,
                                "end": seg.end_time,
                                "text": seg.transcript,
                            }
                            for seg in asr_data.utterances
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            output_file = out_dir / f"{stem}_bcut.txt"
            output_file.write_text(result["text"], encoding="utf-8")

        result["output_file"] = str(output_file)
        logger.info("必剪 ASR 转录完成，字数: %d", len(result["text"]))

    except BcutAPIError as api_error:
        result["error"] = f"必剪 ASR 错误: {api_error}"
        logger.warning("必剪 ASR 错误: %s", api_error)
    except Exception as unexpected_error:
        result["error"] = f"转录异常: {unexpected_error}"
        logger.warning("必剪 ASR 异常: %s", unexpected_error)

    return result


def transcribe_video_with_bcut(
    video_path: str,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """从视频提取音频并使用必剪 ASR 转录（一站式方案）。

    Args:
        video_path: 视频文件路径。
        output_dir: 输出目录。

    Returns:
        {"success": bool, "text": str, "audio_file": str|None, "output_file": str|None, "error": str|None}
    """
    result: dict[str, Any] = {
        "success": False,
        "text": "",
        "audio_file": None,
        "output_file": None,
        "error": None,
    }

    video_path_obj = Path(video_path)
    if not video_path_obj.exists():
        result["error"] = f"视频文件不存在: {video_path}"
        return result

    work_dir = Path(output_dir) if output_dir else video_path_obj.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    # 提取音频
    audio_path = work_dir / f"{video_path_obj.stem}_audio.wav"
    logger.info("从视频提取音频...")

    if not extract_audio_with_ffmpeg(str(video_path_obj), str(audio_path)):
        result["error"] = "音频提取失败（需要安装 ffmpeg）"
        return result

    result["audio_file"] = str(audio_path)
    logger.info("音频提取完成: %s", audio_path.name)

    # 使用 bcut 转录
    transcribe_result = transcribe_with_bcut(str(audio_path), str(work_dir), "txt")

    if transcribe_result["success"]:
        result["success"] = True
        result["text"] = transcribe_result["text"]
        result["output_file"] = transcribe_result["output_file"]
    else:
        result["error"] = transcribe_result["error"]

    return result
