# Douyin Automation Toolkit (抖音自动化技能合集)

这是一个专为抖音（douyin.com）设计的自动化操作工具包。它通过模拟真实浏览器行为、持久化 Cookie 管理以及注入反爬脚本，实现了稳定的登录、数据采集、社交互动和内容发布功能。

## 🚀 核心特性

*   **稳定登录**：支持扫码登录和手机验证码登录，自动处理登录态持久化。
*   **反爬规避**：内置 `stealth.js` 注入和 Chrome Profile 管理，有效降低风控风险。
*   **全功能采集**：支持搜索视频、获取主页信息、抓取评论、提取字幕及热门榜单数据。
*   **自动化互动**：支持点赞、收藏、评论及回复等社交操作。
*   **内容发布**：支持发布普通视频、图文笔记、文章及全景视频。
*   **素材管理**：内置向量数据库支持，实现素材的智能搜索与同步。

## 🛠️ 环境要求

*   Python 3.11+
*   [uv](https://github.com/astral-sh/uv) (推荐的 Python 包管理器)
*   Google Chrome 浏览器

## 📦 安装与运行

1.  **克隆仓库**
    ```bash
    git clone https://github.com/YOUR_USERNAME/douyin-automation-toolkit.git
    cd douyin-automation-toolkit
    ```

2.  **安装依赖**
    ```bash
    uv sync
    ```

3.  **登录账号**
    ```bash
    uv run python scripts/cli.py login
    ```
    按照终端提示完成扫码或输入验证码。

4.  **执行任务**
    *   **搜索视频**: `uv run python scripts/cli.py search-videos --keyword "AI教程" --count 10`
    *   **查看主页**: `uv run python scripts/cli.py user-profile --uid "MS4wLjABAAAA..."`
    *   **发布视频**: `uv run python scripts/cli.py publish-video --file "path/to/video.mp4" --title "我的第一个视频"`

## ⚠️ 免责声明

本工具仅供学习和技术研究使用。请遵守抖音平台的服务条款及相关法律法规，严禁用于任何非法用途或恶意攻击行为。因使用本工具产生的任何后果，使用者需自行承担全部责任。

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。
