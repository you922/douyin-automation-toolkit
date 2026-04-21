"""素材语义搜索（抖音）。

根据文本查询从向量数据库（douyin_material collection）中搜索匹配的图片/视频素材。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .config import get_material_config
from .vector import _get_collection

logger = logging.getLogger(__name__)


def search_materials(
    query: str,
    top_n: int | None = None,
    media_type: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据文本查询搜索匹配的素材。

    Args:
        query: 搜索文本（如发布内容的标题+正文）。
        top_n: 返回的最大结果数，为 None 时使用配置中的 TOP_N。
        media_type: 可选过滤，image 或 video。
        config: 素材管理配置，为 None 时自动加载。

    Returns:
        包含 results 和 count 的字典。每个 result 包含：
        file_path, file_name, media_type, description, score。
    """
    if config is None:
        config = get_material_config()

    if top_n is None:
        top_n = config.get("TOP_N", 3)

    if not query.strip():
        return {"results": [], "count": 0}

    try:
        collection = _get_collection(config)
    except Exception as e:
        return {
            "results": [],
            "count": 0,
            "error": f"向量数据库初始化失败: {e}",
        }

    total_count = collection.count()
    if total_count == 0:
        return {
            "results": [],
            "count": 0,
            "message": "素材库为空，请先添加素材目录",
        }

    # 构建查询参数
    query_kwargs: dict[str, Any] = {
        "query_texts": [query],
        "n_results": min(top_n, total_count),
    }
    if media_type:
        query_kwargs["where"] = {"media_type": media_type}

    try:
        raw_results = collection.query(**query_kwargs)
    except Exception as e:
        return {"results": [], "count": 0, "error": str(e)}

    ids = raw_results.get("ids", [[]])[0] or []
    distances = raw_results.get("distances", [[]])[0] or []
    metadatas = raw_results.get("metadatas", [[]])[0] or []
    documents = raw_results.get("documents", [[]])[0] or []

    results = []
    for i, material_id in enumerate(ids):
        meta = metadatas[i] if i < len(metadatas) else {}
        doc = documents[i] if i < len(documents) else ""
        dist = distances[i] if i < len(distances) else 0

        file_path = meta.get("file_path", "")

        # 跳过本地已删除的文件
        if file_path and not os.path.exists(file_path):
            logger.warning("素材文件已不存在，跳过: %s", file_path)
            continue

        # cosine distance → similarity score
        score = max(0.0, 1.0 - dist) if dist <= 1 else max(0.0, 1.0 - dist)

        results.append(
            {
                "id": material_id,
                "file_path": file_path,
                "file_name": meta.get("file_name", ""),
                "media_type": meta.get("media_type", ""),
                "file_size": meta.get("file_size", 0),
                "description": doc,
                "score": round(score, 4),
            }
        )

    return {"results": results, "count": len(results)}


def search_images_for_publish(
    title: str,
    content: str,
    top_n: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为发布内容搜索匹配的图片素材。

    将标题和正文合并为查询文本，仅搜索图片类型素材。

    Args:
        title: 发布标题。
        content: 发布正文。
        top_n: 返回的最大结果数。
        config: 素材管理配置。

    Returns:
        搜索结果字典。
    """
    query = f"{title} {content}".strip()
    return search_materials(query=query, top_n=top_n, media_type="image", config=config)


def search_videos_for_publish(
    title: str,
    content: str,
    top_n: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为发布内容搜索匹配的视频素材。

    Args:
        title: 发布标题。
        content: 发布正文。
        top_n: 返回的最大结果数。
        config: 素材管理配置。

    Returns:
        搜索结果字典。
    """
    query = f"{title} {content}".strip()
    return search_materials(query=query, top_n=top_n, media_type="video", config=config)
