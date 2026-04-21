"""素材同步管理（抖音）。

扫描配置的素材目录，将新增文件入库，删除已不存在的文件记录。
保持向量数据库（douyin_material collection）与本地文件系统一致。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .config import (
    get_material_config,
    get_supported_extensions,
)
from .vector import (
    _get_collection,
    add_material,
    remove_material,
)

logger = logging.getLogger(__name__)


def _scan_directory(
    directory: str,
    supported_extensions: set[str],
) -> list[str]:
    """递归扫描目录，返回所有支持的素材文件路径。

    Args:
        directory: 目录路径。
        supported_extensions: 支持的文件扩展名集合。

    Returns:
        文件绝对路径列表。
    """
    files = []
    directory = os.path.abspath(directory)

    if not os.path.isdir(directory):
        logger.warning("目录不存在: %s", directory)
        return files

    for root, _dirs, filenames in os.walk(directory):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in supported_extensions:
                filepath = os.path.join(root, filename)
                files.append(filepath)

    return files


def scan_all_directories(config: dict[str, Any] | None = None) -> list[str]:
    """扫描所有配置的素材目录，返回全部素材文件路径。

    Args:
        config: 素材管理配置。

    Returns:
        所有素材文件的绝对路径列表。
    """
    if config is None:
        config = get_material_config()

    image_dirs = config.get("IMAGE_DIRS", [])
    supported_extensions = get_supported_extensions(config)

    all_files = []
    for directory in image_dirs:
        directory = os.path.expanduser(directory)
        files = _scan_directory(directory, supported_extensions)
        all_files.extend(files)
        logger.info("扫描目录 %s: 发现 %d 个素材文件", directory, len(files))

    return all_files


def sync_materials(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """同步素材库：新增入库 + 删除已不存在的记录。

    Args:
        config: 素材管理配置。

    Returns:
        同步结果字典，包含 added, removed, skipped, errors 等统计。
    """
    if config is None:
        config = get_material_config()

    image_dirs = config.get("IMAGE_DIRS", [])
    if not image_dirs:
        return {
            "status": "error",
            "error": "未配置素材目录，请先通过 material-config 添加目录",
        }

    # 扫描本地文件
    local_files = set(scan_all_directories(config))
    logger.info("本地素材文件总数: %d", len(local_files))

    # 获取数据库中已有的素材
    try:
        collection = _get_collection(config)
        existing = collection.get()
    except Exception as e:
        return {"status": "error", "error": f"向量数据库访问失败: {e}"}

    existing_ids = existing.get("ids") or []
    existing_metadatas = existing.get("metadatas") or []

    # 构建已有素材的 file_path → id 映射
    db_path_to_id: dict[str, str] = {}
    for i, material_id in enumerate(existing_ids):
        meta = existing_metadatas[i] if i < len(existing_metadatas) else {}
        file_path = meta.get("file_path", "")
        if file_path:
            db_path_to_id[file_path] = material_id

    db_paths = set(db_path_to_id.keys())

    # 计算差异
    new_files = local_files - db_paths
    removed_files = db_paths - local_files
    existing_files = local_files & db_paths

    added_count = 0
    removed_count = 0
    skipped_count = len(existing_files)
    error_count = 0
    errors: list[str] = []

    # 删除已不存在的素材
    for filepath in removed_files:
        material_id = db_path_to_id[filepath]
        result = remove_material(material_id)
        if result.get("status") == "ok":
            removed_count += 1
            logger.info("已删除不存在的素材: %s", filepath)
        else:
            error_count += 1
            errors.append(f"删除失败 {filepath}: {result.get('error', '未知错误')}")

    # 新增素材
    for filepath in new_files:
        try:
            result = add_material(filepath, config)
            status = result.get("status", "")
            if status == "ok":
                added_count += 1
            elif status == "exists":
                skipped_count += 1
            else:
                error_count += 1
                errors.append(f"入库失败 {filepath}: {result.get('error', '未知错误')}")
        except Exception as e:
            error_count += 1
            errors.append(f"入库异常 {filepath}: {e}")
            logger.error("素材入库异常: %s - %s", filepath, e)

    result_dict: dict[str, Any] = {
        "status": "ok",
        "added": added_count,
        "removed": removed_count,
        "skipped": skipped_count,
        "errors": error_count,
        "total_local": len(local_files),
        "total_db": added_count + skipped_count,
    }

    if errors:
        result_dict["error_details"] = errors

    logger.info(
        "同步完成: 新增 %d, 删除 %d, 跳过 %d, 错误 %d",
        added_count,
        removed_count,
        skipped_count,
        error_count,
    )

    return result_dict


def add_directory(
    directory: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """添加素材目录并立即同步该目录下的素材。

    Args:
        directory: 要添加的目录路径。
        config: 素材管理配置。

    Returns:
        操作结果字典。
    """
    from .config import update_material_config

    directory = os.path.abspath(os.path.expanduser(directory))

    if not os.path.isdir(directory):
        return {"status": "error", "error": f"目录不存在: {directory}"}

    if config is None:
        config = get_material_config()

    current_dirs = list(config.get("IMAGE_DIRS", []))
    if directory in current_dirs:
        return {"status": "exists", "message": f"目录已存在: {directory}"}

    # 更新配置
    current_dirs.append(directory)
    update_material_config(IMAGE_DIRS=current_dirs)

    # 扫描并入库该目录下的素材
    supported_extensions = get_supported_extensions(config)
    files = _scan_directory(directory, supported_extensions)

    if not files:
        return {
            "status": "ok",
            "message": f"目录已添加，但未发现素材文件: {directory}",
            "directory": directory,
            "files_found": 0,
        }

    added_count = 0
    error_count = 0
    errors: list[str] = []

    # 重新加载配置（因为刚更新了 IMAGE_DIRS）
    updated_config = get_material_config()

    for filepath in files:
        try:
            result = add_material(filepath, updated_config)
            if result.get("status") in ("ok", "exists"):
                added_count += 1
            else:
                error_count += 1
                errors.append(f"{filepath}: {result.get('error', '未知错误')}")
        except Exception as e:
            error_count += 1
            errors.append(f"{filepath}: {e}")

    result_dict: dict[str, Any] = {
        "status": "ok",
        "directory": directory,
        "files_found": len(files),
        "added": added_count,
        "errors": error_count,
    }

    if errors:
        result_dict["error_details"] = errors

    return result_dict


def remove_directory(
    directory: str,
    keep_db: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """移除素材目录，可选同时删除该目录下的素材记录。

    Args:
        directory: 要移除的目录路径。
        keep_db: 为 True 时保留数据库记录，仅从配置中移除目录。
        config: 素材管理配置。

    Returns:
        操作结果字典。
    """
    from .config import update_material_config

    directory = os.path.abspath(os.path.expanduser(directory))

    if config is None:
        config = get_material_config()

    current_dirs = list(config.get("IMAGE_DIRS", []))

    if directory not in current_dirs:
        return {"status": "not_found", "message": f"目录未在配置中: {directory}"}

    # 更新配置
    current_dirs.remove(directory)
    update_material_config(IMAGE_DIRS=current_dirs)

    removed_count = 0
    if not keep_db:
        try:
            collection = _get_collection(config)
            # 查找该目录下的所有素材
            all_materials = collection.get()
            ids_to_remove = []
            for i, material_id in enumerate(all_materials.get("ids", [])):
                meta = (
                    (all_materials.get("metadatas") or [])[i]
                    if i < len(all_materials.get("metadatas", []))
                    else {}
                )
                file_path = meta.get("file_path", "")
                if file_path.startswith(directory + os.sep) or file_path.startswith(
                    directory + "/"
                ):
                    ids_to_remove.append(material_id)

            if ids_to_remove:
                collection.delete(ids=ids_to_remove)
                removed_count = len(ids_to_remove)
        except Exception as e:
            return {
                "status": "partial",
                "message": f"目录已从配置中移除，但清理数据库时出错: {e}",
                "directory": directory,
            }

    return {
        "status": "ok",
        "directory": directory,
        "removed_from_db": removed_count,
        "message": f"目录已移除，清理了 {removed_count} 条素材记录",
    }
