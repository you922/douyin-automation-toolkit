"""素材向量化与存储（抖音）。

流程：图片/视频 → 大模型生成描述 → 向量化 → Chroma 存储。
向量化优先使用本地 SentenceTransformer（BAAI/bge-small-zh-v1.5），
本地模型不可用时回退到 OpenAI Embedding API。
视频通过截取关键帧生成描述。

与小红书共用 ~/.dingclaw/material/ 路径和配置，
抖音使用独立的 collection：douyin_material。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any

from .config import (
    LOCAL_EMBEDDING_MODEL_DIR,
    get_chroma_db_path,
    get_material_config,
    is_image_file,
    is_local_embedding_model_available,
    is_sentence_transformers_installed,
    is_video_file,
)

logger = logging.getLogger(__name__)

# 抖音使用独立的 collection，与小红书（xhs_material）隔离
COLLECTION_NAME = "douyin_material"


def _compute_file_hash(filepath: str) -> str:
    """计算文件的 SHA256 哈希值（用于唯一标识）。"""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _encode_image_to_base64(filepath: str) -> str:
    """将图片文件编码为 base64 字符串。"""
    with open(filepath, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_image_mime_type(filepath: str) -> str:
    """根据文件扩展名获取 MIME 类型。"""
    ext = os.path.splitext(filepath)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return mime_map.get(ext, "image/jpeg")


def _extract_video_keyframes(video_path: str, max_frames: int = 3) -> list[str]:
    """从视频中截取关键帧，返回临时图片文件路径列表。

    均匀截取 max_frames 帧，保存为临时 JPEG 文件。

    Args:
        video_path: 视频文件路径。
        max_frames: 最多截取的帧数。

    Returns:
        临时关键帧图片路径列表。
    """
    import tempfile

    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("无法打开视频文件: %s", video_path)
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        cap.release()
        return []

    frame_indices = []
    if total_frames <= max_frames:
        frame_indices = list(range(total_frames))
    else:
        step = total_frames / (max_frames + 1)
        frame_indices = [int(step * (i + 1)) for i in range(max_frames)]

    keyframe_paths = []
    temp_dir = tempfile.mkdtemp(prefix="douyin_material_")

    for idx, frame_idx in enumerate(frame_indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue
        frame_path = os.path.join(temp_dir, f"keyframe_{idx}.jpg")
        cv2.imwrite(frame_path, frame)
        keyframe_paths.append(frame_path)

    cap.release()
    return keyframe_paths


def _generate_image_description(
    image_path: str,
    config: dict[str, Any],
) -> str:
    """调用大模型生成图片的文字描述。

    Args:
        image_path: 图片文件路径。
        config: 素材管理配置。

    Returns:
        图片的文字描述。
    """
    from openai import OpenAI

    api_key = config.get("API_KEY", "")
    base_url = config.get("BASE_URL", "https://api.openai.com/v1")
    model_name = config.get("MODEL_NAME", "gpt-4o")

    if not api_key:
        raise ValueError("未配置 API_KEY，请先通过 material-config 设置大模型 API 密钥")

    client = OpenAI(api_key=api_key, base_url=base_url)

    image_base64 = _encode_image_to_base64(image_path)
    mime_type = _get_image_mime_type(image_path)

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请用中文详细描述这张图片的内容，包括：主题、场景、物体、颜色、"
                            "氛围、风格等。描述要全面准确，便于后续通过文字搜索匹配到这张图片。"
                            "直接输出描述，不要加前缀。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{image_base64}",
                        },
                    },
                ],
            }
        ],
    )

    description = response.choices[0].message.content or ""
    return description.strip()


def _generate_video_description(
    video_path: str,
    config: dict[str, Any],
) -> str:
    """通过截取关键帧生成视频的文字描述。

    Args:
        video_path: 视频文件路径。
        config: 素材管理配置。

    Returns:
        视频的综合文字描述。
    """
    import shutil

    keyframe_paths = _extract_video_keyframes(video_path, max_frames=3)
    if not keyframe_paths:
        raise ValueError(f"无法从视频中截取关键帧: {video_path}")

    try:
        from openai import OpenAI

        api_key = config.get("API_KEY", "")
        base_url = config.get("BASE_URL", "https://api.openai.com/v1")
        model_name = config.get("MODEL_NAME", "gpt-4o")

        if not api_key:
            raise ValueError("未配置 API_KEY，请先通过 material-config 设置大模型 API 密钥")

        client = OpenAI(api_key=api_key, base_url=base_url)

        image_contents = []
        for frame_path in keyframe_paths:
            image_base64 = _encode_image_to_base64(frame_path)
            image_contents.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}",
                    },
                }
            )

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "这是一个视频的关键帧截图。请用中文综合描述这个视频的内容，"
                                "包括：主题、场景、动作、物体、颜色、氛围、风格等。"
                                "描述要全面准确，便于后续通过文字搜索匹配到这个视频。"
                                "直接输出描述，不要加前缀。"
                            ),
                        },
                        *image_contents,
                    ],
                }
            ],
            max_tokens=500,
        )

        description = response.choices[0].message.content or ""
        return description.strip()
    finally:
        # 清理临时关键帧文件
        if keyframe_paths:
            temp_dir = os.path.dirname(keyframe_paths[0])
            shutil.rmtree(temp_dir, ignore_errors=True)


def generate_description(filepath: str, config: dict[str, Any] | None = None) -> str:
    """为图片或视频生成文字描述。

    Args:
        filepath: 文件路径。
        config: 素材管理配置，为 None 时自动加载。

    Returns:
        文字描述。
    """
    if config is None:
        config = get_material_config()

    if is_image_file(filepath, config):
        return _generate_image_description(filepath, config)
    elif is_video_file(filepath, config):
        return _generate_video_description(filepath, config)
    else:
        raise ValueError(f"不支持的文件类型: {filepath}")


def _get_embedding_function(config: dict[str, Any]):
    """获取 Embedding 函数（Chroma 兼容）。

    优先使用本地 SentenceTransformer 模型（BAAI/bge-small-zh-v1.5），
    本地模型不可用时回退到 OpenAI Embedding API（使用 EMBEDDING_MODEL_NAME 配置）。
    """
    if is_sentence_transformers_installed() and is_local_embedding_model_available():
        from chromadb.utils.embedding_functions import (
            SentenceTransformerEmbeddingFunction,
        )

        logger.info("使用本地 SentenceTransformer 模型: %s", LOCAL_EMBEDDING_MODEL_DIR)
        return SentenceTransformerEmbeddingFunction(
            model_name=str(LOCAL_EMBEDDING_MODEL_DIR),
        )

    from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

    api_key = config.get("API_KEY", "")
    embedding_model = config.get("EMBEDDING_MODEL_NAME", "text-embedding-v3")
    base_url = config.get("BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        raise ValueError(
            "本地 embedding 模型未下载，且未配置 API_KEY。"
            "请先执行 material-download-model 下载本地模型，"
            "或通过 material-config 设置 API_KEY 和 EMBEDDING_MODEL_NAME"
        )

    logger.info("本地模型不可用，使用 OpenAI Embedding API: %s (512维)", embedding_model)
    return OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name=embedding_model,
        api_base=base_url,
        dimensions=512,
    )


def _get_chroma_client():
    """获取 Chroma 持久化客户端。"""
    import chromadb

    db_path = str(get_chroma_db_path())
    return chromadb.PersistentClient(path=db_path)


def _get_collection(config: dict[str, Any] | None = None):
    """获取或创建抖音素材 collection（douyin_material）。"""
    if config is None:
        config = get_material_config()
    client = _get_chroma_client()
    embedding_fn = _get_embedding_function(config)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )


def add_material(filepath: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """将单个素材文件向量化并存入 Chroma（douyin_material collection）。

    Args:
        filepath: 素材文件的绝对路径。
        config: 素材管理配置。

    Returns:
        包含 status, file_path, file_hash, description 的字典。
    """
    if config is None:
        config = get_material_config()

    filepath = os.path.abspath(filepath)
    if not os.path.exists(filepath):
        return {"status": "error", "error": f"文件不存在: {filepath}"}

    file_hash = _compute_file_hash(filepath)
    media_type = "image" if is_image_file(filepath, config) else "video"

    # 检查是否已存在
    collection = _get_collection(config)
    existing = collection.get(ids=[file_hash])
    if existing and existing["ids"]:
        existing_path = (existing["metadatas"] or [{}])[0].get("file_path", "")
        if existing_path == filepath:
            return {
                "status": "exists",
                "file_path": filepath,
                "file_hash": file_hash,
                "message": "素材已存在，跳过",
            }

    # 生成描述
    logger.info("正在为素材生成描述: %s", filepath)
    description = generate_description(filepath, config)
    if not description:
        return {"status": "error", "error": f"无法生成描述: {filepath}"}

    # 存入 Chroma
    metadata = {
        "file_path": filepath,
        "file_name": os.path.basename(filepath),
        "media_type": media_type,
        "file_hash": file_hash,
        "file_size": os.path.getsize(filepath),
    }

    collection.upsert(
        ids=[file_hash],
        documents=[description],
        metadatas=[metadata],
    )

    logger.info("素材已入库: %s (hash=%s)", filepath, file_hash[:12])
    return {
        "status": "ok",
        "file_path": filepath,
        "file_hash": file_hash,
        "media_type": media_type,
        "description": description,
    }


def remove_material(file_hash: str) -> dict[str, str]:
    """从 Chroma 中删除指定素材。

    Args:
        file_hash: 素材的文件哈希。

    Returns:
        操作结果字典。
    """
    try:
        collection = _get_collection()
        collection.delete(ids=[file_hash])
        return {"status": "ok", "message": f"已删除素材: {file_hash[:12]}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def remove_material_by_path(filepath: str) -> dict[str, str]:
    """根据文件路径从 Chroma 中删除素材。

    Args:
        filepath: 素材文件路径。

    Returns:
        操作结果字典。
    """
    filepath = os.path.abspath(filepath)
    try:
        collection = _get_collection()
        results = collection.get(where={"file_path": filepath})
        if results and results["ids"]:
            collection.delete(ids=results["ids"])
            return {"status": "ok", "message": f"已删除素材: {filepath}"}
        return {"status": "not_found", "message": f"未找到素材: {filepath}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def list_materials(media_type: str | None = None) -> list[dict[str, Any]]:
    """列出所有已入库的素材。

    Args:
        media_type: 可选过滤，image 或 video。

    Returns:
        素材列表。
    """
    try:
        collection = _get_collection()
        if media_type:
            results = collection.get(where={"media_type": media_type})
        else:
            results = collection.get()

        materials = []
        ids = results.get("ids") or []
        metadatas = results.get("metadatas") or []
        documents = results.get("documents") or []

        for i, material_id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            doc = documents[i] if i < len(documents) else ""
            materials.append(
                {
                    "id": material_id,
                    "file_path": meta.get("file_path", ""),
                    "file_name": meta.get("file_name", ""),
                    "media_type": meta.get("media_type", ""),
                    "file_size": meta.get("file_size", 0),
                    "description": doc,
                }
            )

        return materials
    except Exception as e:
        logger.error("列出素材失败: %s", e)
        return []


def get_material_count() -> dict[str, int]:
    """获取素材库统计信息。"""
    try:
        collection = _get_collection()
        total = collection.count()

        image_results = collection.get(where={"media_type": "image"})
        image_count = len(image_results.get("ids", []))

        video_results = collection.get(where={"media_type": "video"})
        video_count = len(video_results.get("ids", []))

        return {"total": total, "images": image_count, "videos": video_count}
    except Exception:
        return {"total": 0, "images": 0, "videos": 0}
