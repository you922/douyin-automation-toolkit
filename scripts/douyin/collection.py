"""数据收集与持久化。

将搜索结果、评论、用户信息等保存为 JSON 文件，支持增量追加。
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import OUTPUT_DIR

logger = logging.getLogger(__name__)


def save_search_results(
    keyword: str,
    feeds: list[dict[str, Any]],
    output_dir: str | None = None,
) -> str:
    """保存搜索结果到 JSON 文件。

    Args:
        keyword: 搜索关键词。
        feeds: 搜索结果列表（Feed.to_dict() 格式）。
        output_dir: 输出目录，默认 ~/.dingclaw/store-douyin/output。

    Returns:
        保存的文件路径。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_keyword = _safe_filename(keyword)
    filename = f"search_{safe_keyword}_{timestamp}.json"
    filepath = out_dir / filename

    data = {
        "type": "search",
        "keyword": keyword,
        "platform": "douyin",
        "timestamp": timestamp,
        "count": len(feeds),
        "items": feeds,
    }

    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("搜索结果已保存: %s (%d 条)", filepath, len(feeds))
    return str(filepath)


def save_comments(
    video_url: str,
    comments: list[dict[str, Any]],
    output_dir: str | None = None,
) -> str:
    """保存评论数据到 JSON 文件。

    Args:
        video_url: 视频 URL。
        comments: 评论列表。
        output_dir: 输出目录。

    Returns:
        保存的文件路径。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    m = re.search(r"/video/(\d+)", video_url)
    video_id = m.group(1) if m else "unknown"

    filename = f"comments_{video_id}_{timestamp}.json"
    filepath = out_dir / filename

    data = {
        "type": "comments",
        "video_url": video_url,
        "video_id": video_id,
        "platform": "douyin",
        "timestamp": timestamp,
        "count": len(comments),
        "items": comments,
    }

    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("评论已保存: %s (%d 条)", filepath, len(comments))
    return str(filepath)


def save_user_profile(
    profile_data: dict[str, Any],
    output_dir: str | None = None,
) -> str:
    """保存用户主页数据到 JSON 文件。

    Args:
        profile_data: UserProfileResponse.to_dict() 格式。
        output_dir: 输出目录。

    Returns:
        保存的文件路径。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    user_info = profile_data.get("user_basic_info", {})
    nickname = _safe_filename(user_info.get("nickname", "unknown"))

    filename = f"profile_{nickname}_{timestamp}.json"
    filepath = out_dir / filename

    data = {
        "type": "user_profile",
        "platform": "douyin",
        "timestamp": timestamp,
        **profile_data,
    }

    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("用户主页已保存: %s", filepath)
    return str(filepath)


def save_interact_results(
    results: list[dict[str, Any]],
    output_dir: str | None = None,
) -> str:
    """保存互动操作结果到 JSON 文件。

    Args:
        results: ActionResult.to_dict() 格式列表。
        output_dir: 输出目录。

    Returns:
        保存的文件路径。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"interact_{timestamp}.json"
    filepath = out_dir / filename

    data = {
        "type": "interact",
        "platform": "douyin",
        "timestamp": timestamp,
        "count": len(results),
        "items": results,
    }

    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("互动结果已保存: %s (%d 条)", filepath, len(results))
    return str(filepath)


def save_publish_result(
    publish_data: dict[str, Any],
    output_dir: str | None = None,
) -> str:
    """保存发布结果到 JSON 文件。

    Args:
        publish_data: 发布结果字典。
        output_dir: 输出目录。

    Returns:
        保存的文件路径。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"publish_{timestamp}.json"
    filepath = out_dir / filename

    data = {
        "type": "publish",
        "platform": "douyin",
        "timestamp": timestamp,
        **publish_data,
    }

    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("发布结果已保存: %s", filepath)
    return str(filepath)


def load_latest_data(
    data_type: str,
    output_dir: str | None = None,
) -> dict[str, Any] | None:
    """加载最新的数据文件。

    Args:
        data_type: 数据类型前缀（search/comments/profile/interact/publish）。
        output_dir: 输出目录。

    Returns:
        数据字典，无数据返回 None。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    if not out_dir.exists():
        return None

    files = sorted(
        out_dir.glob(f"{data_type}_*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return None

    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("加载数据失败: %s, 错误: %s", files[0], e)
        return None


def list_data_files(
    data_type: str | None = None,
    output_dir: str | None = None,
) -> list[dict[str, Any]]:
    """列出数据文件。

    Args:
        data_type: 数据类型前缀，None 表示所有。
        output_dir: 输出目录。

    Returns:
        文件信息列表。
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    if not out_dir.exists():
        return []

    pattern = f"{data_type}_*.json" if data_type else "*.json"
    files = sorted(
        out_dir.glob(pattern),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    result = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.append({
                "filename": f.name,
                "path": str(f),
                "type": data.get("type", "unknown"),
                "timestamp": data.get("timestamp", ""),
                "count": data.get("count", 0),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        except (json.JSONDecodeError, OSError):
            result.append({
                "filename": f.name,
                "path": str(f),
                "type": "unknown",
                "timestamp": "",
                "count": 0,
                "size_kb": round(f.stat().st_size / 1024, 1),
            })

    return result


def _safe_filename(text: str, max_len: int = 30) -> str:
    """将文本转为安全的文件名。"""
    safe = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", text)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe[:max_len] if safe else "unknown"
