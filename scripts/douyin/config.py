"""配置管理（CDP 方案）。

所有配置通过环境变量或默认值获取，不依赖配置文件。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# ========== 路径常量 ==========
# 数据根目录
DATA_DIR = Path.home() / ".dingclaw" / "store-douyin"
# Chrome Profile 目录
CHROME_PROFILE_DIR = DATA_DIR / "chrome-profile"
# 下载目录
DOWNLOAD_DIR = DATA_DIR / "downloads"
# 输出目录
OUTPUT_DIR = DATA_DIR / "output"

# ========== CDP 配置 ==========
CDP_PORT = int(os.getenv("DOUYIN_CDP_PORT", "9222"))
CDP_HOST = os.getenv("DOUYIN_CDP_HOST", "127.0.0.1")

# ========== 抖音域名 ==========
DOUYIN_DOMAIN = ".douyin.com"

# ========== 日志 ==========
_logger: logging.Logger | None = None


def get_logger(name: str = "douyin") -> logging.Logger:
    """获取日志实例（单例）。"""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        # 与 cli 一致：日志走 stderr，stdout 留给机器可读 JSON（subprocess 只读 stdout 时不受污染）
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
        )
        logger.addHandler(handler)

    _logger = logger
    return logger


def ensure_dirs() -> None:
    """确保所有必要目录存在。"""
    for d in (DATA_DIR, CHROME_PROFILE_DIR, DOWNLOAD_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
