# -*- coding: utf-8 -*-
"""浏览器连接管理（CDP 方案）。

通过 chrome_launcher 确保 Chrome 可用，通过 cdp.Browser 建立连接。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# 确保 scripts 目录在 sys.path 中
scripts_dir = Path(__file__).resolve().parent.parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from chrome_launcher import DEFAULT_PORT, ensure_chrome, kill_chrome
from .cdp import Browser, Page

logger = logging.getLogger(__name__)


def get_browser(port: int = DEFAULT_PORT) -> Browser:
    """获取 Browser 实例（确保 Chrome 已启动并建立 CDP 连接）。

    Returns:
        已连接的 Browser 实例。

    Raises:
        RuntimeError: Chrome 启动失败或连接失败。
    """
    if not ensure_chrome(port=port):
        raise RuntimeError(
            "Chrome 启动失败，请确认已安装 Chrome 或设置 CHROME_BIN 环境变量"
        )

    browser = Browser(port=port)
    browser.connect()
    return browser


def get_page(port: int = DEFAULT_PORT) -> Page:
    """获取可用的 Page 实例（优先复用已有抖音页面，否则创建新页面）。

    Returns:
        CDP Page 实例。

    Raises:
        RuntimeError: 无法获取页面。
    """
    browser = get_browser(port=port)

    # 优先复用已有页面
    page = browser.get_existing_page()
    if page:
        logger.info("复用已有页面")
        return page

    # 创建新页面
    page = browser.new_page("https://www.douyin.com")
    logger.info("创建新页面")
    return page


def close_browser(port: int = DEFAULT_PORT) -> None:
    """关闭 Chrome 浏览器。"""
    kill_chrome(port=port)
    logger.info("浏览器已关闭")
