"""Cookie 文件持久化。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认 Cookie 存储路径
DEFAULT_COOKIE_DIR = Path.home() / ".dingclaw" / "store-douyin" / "cookies"


def get_cookie_path(account: str = "default") -> Path:
    """获取指定账号的 Cookie 文件路径。"""
    cookie_dir = DEFAULT_COOKIE_DIR
    cookie_dir.mkdir(parents=True, exist_ok=True)
    return cookie_dir / f"{account}.json"


def get_cookies_file_path(account: str = "default") -> str:
    """获取 cookies 文件路径（与小红书接口一致）。"""
    return str(get_cookie_path(account))


def save_cookies(cookies: list[dict], account: str = "default") -> None:
    """保存 Cookie 到文件。"""
    cookie_path = get_cookie_path(account)
    with open(cookie_path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    logger.info("Cookie 已保存到: %s", cookie_path)


def load_cookies(account: str = "default") -> list[dict] | None:
    """从文件加载 Cookie。"""
    cookie_path = get_cookie_path(account)
    if not cookie_path.exists():
        return None
    try:
        with open(cookie_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载 Cookie 失败: %s", e)
        return None


def delete_cookies(account: str = "default") -> None:
    """删除 Cookie 文件。"""
    cookie_path = get_cookie_path(account)
    if cookie_path.exists():
        cookie_path.unlink()
        logger.info("Cookie 已删除: %s", cookie_path)


def cookies_exist(account: str = "default") -> bool:
    """检查 Cookie 文件是否存在。"""
    return get_cookie_path(account).exists()


def cookies_to_string(cookies: list[dict] | None) -> str:
    """将 Cookie 列表转为 name=value; name2=value2 格式（用于 yt-dlp 等）。"""
    if not cookies:
        return ""
    parts = []
    for c in cookies:
        if isinstance(c, dict) and c.get("name") and c.get("value"):
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)