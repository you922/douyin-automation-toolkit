"""素材管理配置。

配置文件位置：~/.dingclaw/material/config.py
与小红书共用同一配置文件和 Chroma 数据库路径。
包含：IMAGE_DIRS, API_KEY, MODEL_NAME, BASE_URL, EMBEDDING_MODEL_NAME, TOP_N 等。
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MATERIAL_DIR = Path.home() / ".dingclaw" / "material"
CONFIG_FILE = MATERIAL_DIR / "config.py"
CHROMA_DB_DIR = MATERIAL_DIR / "chroma_db"
LOCAL_EMBEDDING_MODEL_DIR = MATERIAL_DIR / "textEmbeddingModel"
LOCAL_EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
HF_MIRROR_URL = "https://hf-mirror.com"

# 配置文件模板
_CONFIG_TEMPLATE = '''"""素材管理配置文件。

通过聊天修改配置，或直接编辑此文件。
"""

# 素材目录列表（绝对路径）
IMAGE_DIRS: list[str] = []

# 大模型配置（用于生成图片/视频描述，OpenAI 标准接口）
# 注意：必须使用支持多模态（能识别理解图片和视频）的大模型，如 gpt-4o、qwen-vl 等
API_KEY: str = ""
MODEL_NAME: str = "gpt-4o"
BASE_URL: str = "https://api.openai.com/v1"

# Embedding 模型名（用于向量化，与 MODEL_NAME 分开配置）
EMBEDDING_MODEL_NAME: str = "text-embedding-v3"

# 搜索返回的素材数量
TOP_N: int = 3

# 支持的图片扩展名
IMAGE_EXTENSIONS: list[str] = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]

# 支持的视频扩展名
VIDEO_EXTENSIONS: list[str] = [".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"]
'''

# 所有可配置的字段及其类型
_CONFIG_FIELDS = {
    "IMAGE_DIRS": list,
    "API_KEY": str,
    "MODEL_NAME": str,
    "BASE_URL": str,
    "EMBEDDING_MODEL_NAME": str,
    "TOP_N": int,
    "IMAGE_EXTENSIONS": list,
    "VIDEO_EXTENSIONS": list,
}

# 默认值
_DEFAULTS = {
    "IMAGE_DIRS": [],
    "API_KEY": "",
    "MODEL_NAME": "gpt-4o",
    "BASE_URL": "https://api.openai.com/v1",
    "EMBEDDING_MODEL_NAME": "text-embedding-v3",
    "TOP_N": 3,
    "IMAGE_EXTENSIONS": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"],
    "VIDEO_EXTENSIONS": [".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"],
}


def ensure_config_exists() -> Path:
    """确保配置文件存在，不存在则创建默认配置。"""
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
    return CONFIG_FILE


def _load_config_module() -> dict[str, Any]:
    """动态加载配置文件为 Python 模块，返回配置字典。"""
    config_path = ensure_config_exists()
    spec = importlib.util.spec_from_file_location("material_config", str(config_path))
    if spec is None or spec.loader is None:
        return dict(_DEFAULTS)

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return dict(_DEFAULTS)

    result = {}
    for key, default_value in _DEFAULTS.items():
        result[key] = getattr(module, key, default_value)
    return result


def get_material_config() -> dict[str, Any]:
    """获取素材管理配置。

    Returns:
        包含所有配置项的字典。
    """
    return _load_config_module()


def update_material_config(**kwargs: Any) -> dict[str, Any]:
    """更新配置文件中的指定字段。

    Args:
        **kwargs: 要更新的字段名和值。

    Returns:
        更新后的完整配置字典。

    Raises:
        ValueError: 字段名不合法或类型不匹配。
    """
    for key, value in kwargs.items():
        if key not in _CONFIG_FIELDS:
            raise ValueError(f"未知配置项: {key}，支持: {list(_CONFIG_FIELDS.keys())}")
        expected_type = _CONFIG_FIELDS[key]
        if not isinstance(value, expected_type):
            raise ValueError(
                f"配置项 {key} 类型错误: 期望 {expected_type.__name__}，实际 {type(value).__name__}"
            )

    current = get_material_config()
    current.update(kwargs)

    lines = [
        '"""素材管理配置文件。\n',
        "\n",
        "通过聊天修改配置，或直接编辑此文件。\n",
        '"""\n',
        "\n",
    ]

    for key, value in current.items():
        type_hint = _CONFIG_FIELDS.get(key)
        if type_hint == list:
            if key == "IMAGE_DIRS":
                lines.append("# 素材目录列表（绝对路径）\n")
            elif key == "IMAGE_EXTENSIONS":
                lines.append("# 支持的图片扩展名\n")
            elif key == "VIDEO_EXTENSIONS":
                lines.append("# 支持的视频扩展名\n")
            lines.append(f"{key}: list[str] = {value!r}\n\n")
        elif type_hint == int:
            if key == "TOP_N":
                lines.append("# 搜索返回的素材数量\n")
            lines.append(f"{key}: int = {value!r}\n\n")
        else:
            if key == "API_KEY":
                lines.append("# 大模型配置（用于生成图片/视频描述，OpenAI 标准接口）\n")
                lines.append(
                    "# 注意：必须使用支持多模态（能识别理解图片和视频）的大模型，如 gpt-4o、qwen-vl 等\n"
                )
            elif key == "EMBEDDING_MODEL_NAME":
                lines.append(
                    "# Embedding 模型名（用于向量化，与 MODEL_NAME 分开配置）\n"
                )
            lines.append(f"{key}: str = {value!r}\n\n")

    ensure_config_exists()
    CONFIG_FILE.write_text("".join(lines), encoding="utf-8")
    return current


def get_chroma_db_path() -> Path:
    """获取 Chroma 数据库目录路径。"""
    CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)
    return CHROMA_DB_DIR


def is_chroma_installed() -> bool:
    """检查 chromadb 是否已安装。"""
    try:
        import chromadb  # noqa: F401

        return True
    except ImportError:
        return False


def is_sentence_transformers_installed() -> bool:
    """检查 sentence-transformers 是否已安装。"""
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


def is_local_embedding_model_available() -> bool:
    """检查本地 embedding 模型是否已下载且可用。

    判断依据：模型目录存在且包含 config.json 文件。
    """
    config_json = LOCAL_EMBEDDING_MODEL_DIR / "config.json"
    return LOCAL_EMBEDDING_MODEL_DIR.exists() and config_json.exists()


def download_local_embedding_model() -> dict[str, Any]:
    """下载本地 embedding 模型（BAAI/bge-small-zh-v1.5）到指定目录。

    自动配置 HuggingFace 国内镜像源加速下载。

    Returns:
        包含 status 和 message 的结果字典。
    """
    if is_local_embedding_model_available():
        return {
            "status": "exists",
            "model_name": LOCAL_EMBEDDING_MODEL_NAME,
            "model_dir": str(LOCAL_EMBEDDING_MODEL_DIR),
            "message": "本地 embedding 模型已存在，无需重复下载",
        }

    if not is_sentence_transformers_installed():
        return {
            "status": "error",
            "error": "sentence-transformers 未安装，请先执行: uv pip install sentence-transformers",
        }

    # 配置国内镜像源
    original_endpoint = os.environ.get("HF_ENDPOINT", "")
    os.environ["HF_ENDPOINT"] = HF_MIRROR_URL

    try:
        from sentence_transformers import SentenceTransformer

        LOCAL_EMBEDDING_MODEL_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            "正在从 %s 下载模型 %s 到 %s ...",
            HF_MIRROR_URL,
            LOCAL_EMBEDDING_MODEL_NAME,
            LOCAL_EMBEDDING_MODEL_DIR,
        )

        model = SentenceTransformer(
            LOCAL_EMBEDDING_MODEL_NAME,
            cache_folder=str(LOCAL_EMBEDDING_MODEL_DIR.parent),
        )
        model.save(str(LOCAL_EMBEDDING_MODEL_DIR))

        return {
            "status": "ok",
            "model_name": LOCAL_EMBEDDING_MODEL_NAME,
            "model_dir": str(LOCAL_EMBEDDING_MODEL_DIR),
            "message": f"模型 {LOCAL_EMBEDDING_MODEL_NAME} 下载完成",
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": f"模型下载失败: {exc}",
            "hint": "请检查网络连接，或手动下载模型到 " + str(LOCAL_EMBEDDING_MODEL_DIR),
        }
    finally:
        if original_endpoint:
            os.environ["HF_ENDPOINT"] = original_endpoint
        elif "HF_ENDPOINT" in os.environ:
            del os.environ["HF_ENDPOINT"]


def is_openai_installed() -> bool:
    """检查 openai 是否已安装。"""
    try:
        import openai  # noqa: F401

        return True
    except ImportError:
        return False


def is_pillow_installed() -> bool:
    """检查 Pillow 是否已安装。"""
    try:
        from PIL import Image  # noqa: F401

        return True
    except ImportError:
        return False


def is_opencv_installed() -> bool:
    """检查 opencv-python 是否已安装。"""
    try:
        import cv2  # noqa: F401

        return True
    except ImportError:
        return False


def check_dependencies() -> dict[str, bool]:
    """检查素材管理所需的依赖是否已安装。

    Returns:
        各依赖的安装状态字典。
    """
    deps = {
        "chromadb": is_chroma_installed(),
        "openai": is_openai_installed(),
        "Pillow": is_pillow_installed(),
        "opencv-python": is_opencv_installed(),
    }
    return deps


# 安装时需要带版本约束的包
_INSTALL_SPECS = {
    "numpy": '"numpy<2"',
}


def get_missing_dependencies() -> list[str]:
    """获取未安装的依赖列表（带版本约束）。"""
    deps = check_dependencies()
    missing = []
    for name, installed in deps.items():
        if not installed:
            missing.append(_INSTALL_SPECS.get(name, name))
    return missing


def get_supported_extensions(config: dict[str, Any] | None = None) -> set[str]:
    """获取所有支持的文件扩展名（图片 + 视频）。"""
    if config is None:
        config = get_material_config()
    image_exts = {e.lower() for e in config.get("IMAGE_EXTENSIONS", [])}
    video_exts = {e.lower() for e in config.get("VIDEO_EXTENSIONS", [])}
    return image_exts | video_exts


def is_image_file(filepath: str, config: dict[str, Any] | None = None) -> bool:
    """判断文件是否为支持的图片格式。"""
    if config is None:
        config = get_material_config()
    ext = os.path.splitext(filepath)[1].lower()
    return ext in {e.lower() for e in config.get("IMAGE_EXTENSIONS", [])}


def is_video_file(filepath: str, config: dict[str, Any] | None = None) -> bool:
    """判断文件是否为支持的视频格式。"""
    if config is None:
        config = get_material_config()
    ext = os.path.splitext(filepath)[1].lower()
    return ext in {e.lower() for e in config.get("VIDEO_EXTENSIONS", [])}
