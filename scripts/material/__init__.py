"""素材管理模块（与小红书共用素材库路径 ~/.dingclaw/material/）。

提供本地图片/视频素材的向量化存储、语义搜索、同步管理能力。
素材通过大模型生成描述 → Embedding 向量化 → Chroma 向量数据库存储。

抖音使用独立的 collection：douyin_material
小红书使用独立的 collection：xhs_material
两者共用同一个 Chroma 数据库路径和配置文件。
"""

from .config import (
    check_dependencies,
    download_local_embedding_model as download_embedding_model,
    get_material_config as get_config,
    update_material_config as update_config,
)
from .search import search_materials
from .sync import add_directory, remove_directory, sync_materials
from .vector import get_material_count as get_stats
from .vector import list_materials as _list_materials_raw


def list_materials(media_type: str = "") -> dict:
    """列出所有已入库素材，返回包含 results 和 count 的字典。

    Args:
        media_type: 可选过滤，image 或 video，空字符串表示全部。

    Returns:
        包含 results（素材列表）和 count（数量）的字典。
    """
    items = _list_materials_raw(media_type if media_type else None)
    return {"results": items, "count": len(items)}


__all__ = [
    "check_dependencies",
    "download_embedding_model",
    "get_config",
    "update_config",
    "search_materials",
    "add_directory",
    "remove_directory",
    "sync_materials",
    "get_stats",
    "list_materials",
]
