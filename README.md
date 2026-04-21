# Douyin Automation Toolkit (抖音自动化技能合集)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

这是一个专为抖音（douyin.com）设计的**企业级自动化操作工具包**。它通过模拟真实浏览器行为、持久化 Cookie 管理以及注入反爬脚本，实现了稳定的登录、数据采集、社交互动和内容发布功能。

## 🚀 核心特性

*   **稳定登录体系**：支持扫码登录和手机验证码登录，自动处理登录态持久化与多账号切换。
*   **深度反爬规避**：内置 `stealth.js` 注入和 Chrome Profile 管理，有效降低风控风险，实现长时间稳定运行。
*   **全维度数据采集**：支持关键词搜索、用户主页分析、评论批量抓取、字幕提取及热门榜单监控。
*   **自动化社交互动**：支持点赞、收藏、评论及回复等拟人化社交操作。
*   **多媒体内容发布**：支持发布普通视频、图文笔记、长文章及全景视频，并支持关联热点与位置。
*   **智能素材管理**：内置向量数据库支持，实现素材的智能搜索、去重与同步。

## 🛠️ 环境要求

*   **操作系统**: macOS / Windows / Linux
*   **Python**: 3.11+
*   **浏览器**: Google Chrome (建议最新版)
*   **包管理器**: [uv](https://github.com/astral-sh/uv) (推荐) 或 pip

## 📦 快速开始

### 1. 安装依赖

```bash
git clone https://github.com/you922/douyin-automation-toolkit.git
cd douyin-automation-toolkit
uv sync
```

### 2. 账号登录

首次使用需先进行登录，工具会自动启动 Chrome 并引导你完成验证：

```bash
uv run python scripts/cli.py login
```

### 3. 常用指令示例

| 任务类型 | 命令示例 |
| :--- | :--- |
| **搜索视频** | `uv run python scripts/cli.py search-videos --keyword "AI教程" --count 10` |
| **查看主页** | `uv run python scripts/cli.py user-profile --uid "MS4wLjABAAAA..."` |
| **发布视频** | `uv run python scripts/cli.py publish-video --file "path/to/video.mp4" --title "我的视频"` |
| **获取热榜** | `uv run python scripts/cli.py hot-list` |
| **查看评论** | `uv run python scripts/cli.py get-comments --aweme-id "73xxxxxx"` |

## 📂 项目结构

```text
douyin-automation-toolkit/
├── scripts/                # 核心逻辑代码
│   ├── cli.py              # 统一命令行入口
│   ├── chrome_launcher.py  # 浏览器启动与管理
│   ├── douyin/             # 抖音业务模块 (登录, 搜索, 发布等)
│   └── material/           # 素材管理与向量检索
├── skills/                 # 子技能定义文档 (SKILL.md)
├── prompts/                # AI 提示词模板
├── pyproject.toml          # 项目依赖配置
└── README.md               # 项目说明文档
```

## ⚙️ 高级配置

*   **多账号管理**: 使用 `--account <name>` 参数在不同账号间切换。
*   **代理设置**: 在 `scripts/douyin/config.py` 中配置代理服务器地址。
*   **日志记录**: 所有操作日志默认输出到 `stderr`，JSON 结果输出到 `stdout`，便于脚本集成。

## ⚠️ 免责声明

本工具仅供学习和技术研究使用。请严格遵守抖音平台的服务条款及相关法律法规。**严禁**将本工具用于任何非法用途、恶意攻击或大规模骚扰行为。因违规使用本工具产生的任何法律后果及账号封禁风险，使用者需自行承担全部责任。

## 🤝 贡献指南

欢迎提交 Issue 或 Pull Request！在提交代码前，请确保：
1. 代码符合 PEP 8 规范。
2. 已更新相关的文档或注释。
3. 不提交任何包含个人隐私或敏感信息的文件（如 Cookies）。

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件。
