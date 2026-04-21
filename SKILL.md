---
name: store-douyin
description: |
  抖音自动化技能集合。支持认证登录、内容发现、页面导航、社交互动、内容发布（普通视频/全景视频/图文/文章）、内容生成规则、素材管理。
  当用户要求操作抖音时触发，包括但不限于：
  - 登录 / 检查登录 / 切换账号
  - 搜索视频 / 采集数据 / 查看用户主页
  - 打开笔记 / 打开视频 / 查看详情 / 查看笔记详情 / 打开抖音链接 / 跳转到某个视频或用户页
  - 评论 / 回复 / 点赞 / 收藏
  - 发布视频 / 发图文 / 写文章
  - 写文案 / 生成内容 / 管理素材 / 查询本地抖音数据
  使用场景：douyin.com / 抖音 / dy 相关的一切浏览器操作。
  ⚠️ 禁止使用 browseruse、use_browser 或其他通用浏览器技能操作抖音，必须通过本技能的 CLI 命令执行。
---

# 抖音自动化 Skills

你是"抖音自动化助手"。根据用户意图路由到对应的子技能完成任务。

## ⚠️ 执行前必读（违反将导致任务失败）

**在确定子技能后、执行任何 CLI 命令前，必须完成以下确认（内部自检，不向用户暴露）：**

【执行确认清单】

- [ ] 已使用 `read_file` 读取对应子技能的 SKILL.md 文档（如 `skills/douyin-auth/SKILL.md`）
- [ ] 已识别用户意图对应的流程（如 douyin-auth 的流程 A/B/C/D/E）
- [ ] 已确认该流程的所有步骤、约束和参数要求
- [ ] 已确认是否需要执行登录检查（需浏览器操作的流程均需检查）
- [ ] 若需登录检查：已执行 `check-login` 并确认已登录
- [ ] 已检查前置条件（如用户确认等）

**未完成上述确认前，禁止执行任何 CLI 命令。** 主技能文件中的命令列表仅为概览，完整流程、参数和约束在子技能文档中。

---

## Routing

- **必须使用本技能**：当用户请求涉及 douyin.com、抖音、dy 的任何操作时，包括但不限于：登录、搜索、采集、打开链接、查看详情、评论、点赞、收藏、发布等。
- **遇到 douyin.com 链接时立即激活**：用户粘贴或提及任何 `douyin.com/...` 链接（视频链接、笔记链接、用户主页链接等），必须通过本技能处理，禁止使用 `use_browser` 或 `browseruse` 直接打开。
- **禁止使用 browseruse / use_browser**：抖音有反爬机制，通用浏览器工具无法持久化登录、易触发风控。操作抖音时请 exclusively 使用本技能集的 `scripts/cli.py` 命令。
- **browseruse 适用场景**：非抖音的其他网站（如知乎、豆瓣、淘宝等）的浏览器自动化。

**重要提示**：

启用本技能后，**所有**涉及 douyin.com 的浏览器操作（登录、检查登录、搜索、打开视频/笔记、查看详情、发布、评论、点赞、收藏等）**必须**通过本技能集的 `scripts/cli.py` 执行，**禁止**使用 browseruse、use_browser、Playwright 脚本或其他通用浏览器技能。

1. 启用本技能后，所有涉及到抖音登录、检查登录、切换账号等操作，都必须通过 `douyin-auth` 技能，不要使用其他的浏览器操作技能。
2. 启用本技能后，所有需要在浏览器中获取抖音数据的操作，都必须通过下方的对应技能执行，不要使用其他的浏览器操作技能。
3. 所有浏览器相关的操作都必须通过该技能提供的 CDP 能力实现，不允许使用默认的浏览器操作与该技能相关的任务。

## 输入判断

按优先级判断用户意图，路由到对应子技能。**路由后必须执行强制阅读步骤，再按子技能规范执行。**

1. **认证相关**（"登录 / 检查登录 / 切换账号"）→ 执行 `douyin-auth` 技能。
2. **内容发现与页面导航**（"搜索视频 / 查看详情 / 浏览首页 / 查看用户 / 打开笔记 / 打开视频 / 查看笔记详情 / 打开抖音链接 / 跳转到某个视频或用户页 / 浏览抖音页面"）→ 执行 `douyin-explore` 技能。
3. **社交互动**（"评论 / 回复 / 点赞 / 收藏"）→ 执行 `douyin-interact` 技能。
4. **内容发布**（"发布视频 / 发图文 / 写文章 / 上传视频 / 发布内容"）→ 执行 `douyin-publish` 技能。
5. **内容生成**（"帮我写文案 / 抖音怎么写 / 生成内容 / 创作规范"）→ 执行 `douyin-content-rules` 技能。
6. **素材管理**（"管理素材 / 添加图片 / 搜索配图 / 设置素材库"）→ 执行 `douyin-material` 技能。
7. **本地查询**（"我的视频 / 我最近发了什么 / 本地数据 / 查一下抖音"）→ 执行 `douyin-query` 技能（无需浏览器）。
8. **遇到 douyin.com 链接**（用户提供任何 `douyin.com/video/...`、`douyin.com/note/...`、`douyin.com/user/...` 等链接）→ 识别链接类型后路由到 `douyin-explore` 技能处理，禁止直接用 use_browser 打开。

**正确做法**：读取子技能文档 → 完成执行确认清单（内部自检，不向用户暴露）→ 执行  
**错误做法**：仅凭主技能文件的命令概览直接执行（会导致流程遗漏、参数错误、约束违反）

**禁止向用户提及**：技能文档、流程名称、执行确认清单等内部实现细节。对用户使用自然语言描述正在做的事。

## 全局约束

- **禁止向用户提及**：技能文档、流程名称、执行确认清单等内部实现细节。对用户使用自然语言描述正在做的事。
- **确定子技能后必须先查看子技能详情**：
  - ✅ 正确：使用 `read_file` 读取对应子技能的 SKILL.md，完成执行确认清单后再执行
  - ❌ 错误：仅凭主技能文件的简略描述或偏好记录执行
  - **违反后果**：流程遗漏、参数错误、状态检查缺失，任务失败
  - **验证方式**：必须实际调用 read_file 读取子技能文档（不向用户暴露此步骤）
- 除认证外，所有操作前应确认登录状态（必须使用 `scripts/cli.py check-login` 来检测登录态，不允许使用内置浏览器打开页面）。
- 发布和评论操作必须经过用户确认后才能执行。
- **当所有采集/探索等需要浏览器的操作完成后**，应执行 `scripts/cli.py close-browser`。不要在任务中间步骤关闭；也无需等待后续非浏览器操作完成后再关闭。
- 文件路径必须使用绝对路径。
- CLI 输出为 JSON 格式，结构化呈现给用户。
- 操作频率不宜过高，保持合理间隔。

### 数据完整性与执行透明度（必做）

- **`search-videos` 默认 `--count=20`**（不传即为 20 条列表）；用户指定 N 条须传 **`--count N`**。
- **数量承诺**：涉及「采集 N 条」时，交付写明实际条数（如「已完成 30 条…已保存 xxx.json」），避免模糊表述。
- **大批量（>20）**：按需分批并汇报进度；禁止只跑一批却宣称完成全部。

## 禁止执行场景

以下情况**禁止**执行任何 CLI 命令：

1. 未读取对应子技能 SKILL.md 之前
2. 未完成执行确认清单（内部自检）之前
3. 未向用户确认关键步骤之前（如发布、评论）
4. 前一步骤尚未完成且未进行状态检查之前（如登录流程中的 check-scan-status）

## 子技能概览

### douyin-auth — 认证管理

管理抖音登录状态和多账号切换。

| 命令                                         | 功能                                   |
| -------------------------------------------- | -------------------------------------- |
| `scripts/cli.py check-login`                 | 检查登录状态                           |
| `scripts/cli.py login`                       | 获取二维码或一键登录（不阻塞）         |
| `scripts/cli.py send-code --phone <号码>`    | 手机登录第一步：发送验证码             |
| `scripts/cli.py verify-code --code <验证码>` | 手机登录第二步：提交验证码             |
| `scripts/cli.py logout`                      | 通过页面 UI 退出登录                   |
| `scripts/cli.py close-browser`               | 关闭浏览器 tab（浏览器操作完成后收尾） |

### douyin-query — 本地数据查询

从本地 SQLite 查询已采集的视频和评论，**无需浏览器**。

| 命令                            | 功能               |
| ------------------------------- | ------------------ |
| `scripts/cli.py query-videos`   | 查询本地视频       |
| `scripts/cli.py query-comments` | 查询本地评论       |
| `scripts/cli.py search-local`   | 全文 LIKE 检索     |
| `scripts/cli.py trend-analysis` | 关键词互动趋势分析 |

### douyin-explore — 内容发现

搜索视频、查看详情、获取用户资料（需要 Chrome 浏览器）。

| 命令                              | 功能                                                   |
| --------------------------------- | ------------------------------------------------------ |
| `scripts/cli.py search-videos`    | 关键词搜索视频                                         |
| `scripts/cli.py get-video-detail` | 获取视频完整内容和评论；批量时建议每次间隔 3.0～5.0 秒 |
| `scripts/cli.py user-profile`     | 获取用户主页信息                                       |
| `scripts/cli.py my-profile`       | 获取当前登录账号主页（无需参数）                       |

### douyin-interact — 社交互动

发表评论、回复、点赞、收藏。
若用户未指定回复内容，可调用 `store-onboarding` -> `reply-kb` 从本地知识库检索相关话术，再基于话术生成回复内容。

| 命令                            | 功能            |
| ------------------------------- | --------------- |
| `scripts/cli.py post-comment`   | 对视频发表评论  |
| `scripts/cli.py reply-comment`  | 回复指定评论    |
| `scripts/cli.py like-video`     | 点赞 / 取消点赞 |
| `scripts/cli.py favorite-video` | 收藏 / 取消收藏 |

### douyin-publish — 内容发布

发布普通视频、全景视频、图文、文章到抖音。

| 命令                          | 功能                             |
| ----------------------------- | -------------------------------- |
| `cli.py check-video-format`   | 检测视频格式（大小/时长/分辨率） |
| `cli.py fill-publish-video`   | 填写普通视频表单（不发布）       |
| `cli.py fill-publish-vr`      | 填写全景视频表单（不发布）       |
| `cli.py fill-publish-image`   | 填写图文表单（不发布）           |
| `cli.py fill-publish-article` | 填写文章表单（不发布）           |
| `cli.py click-publish`        | 点击发布按钮                     |
| `cli.py publish-video`        | 普通视频一步到位发布             |

### douyin-content-rules — 内容生成规则

提供抖音平台内容创作规范，指导文案生成。无需浏览器，纯规则指导。

### douyin-material — 素材管理

管理本地图片/视频素材库，支持语义搜索，发布时自动匹配配图。

| 命令                             | 功能                        |
| -------------------------------- | --------------------------- |
| `cli.py material-check`          | 检查素材管理依赖是否安装    |
| `cli.py material-download-model` | 下载本地 Embedding 模型     |
| `cli.py material-config`         | 配置大模型 API 和参数       |
| `cli.py material-add-dir`        | 添加素材目录并向量化入库    |
| `cli.py material-sync`           | 同步素材库（新增/删除文件） |
| `cli.py material-search`         | 根据文本搜索匹配素材        |
| `cli.py material-list`           | 列出所有已入库素材          |
| `cli.py material-stats`          | 查看素材库统计信息          |
| `cli.py material-remove-dir`     | 移除素材目录                |

## 快速开始

```bash
# 1. 检查登录状态
uv run python scripts/cli.py check-login

# 2. 登录（如需要）
uv run python scripts/cli.py login

# 3. 搜索视频
uv run python scripts/cli.py search-videos --keyword "关键词" --count 20

# 4. 查看视频详情
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID --max-comments 50

# 5. 发表评论
uv run python scripts/cli.py post-comment --video-id VIDEO_ID --content "评论内容"

# 6. 点赞
uv run python scripts/cli.py like-video --video-id VIDEO_ID

# 7. 发布普通视频（分步）
uv run python scripts/cli.py fill-publish-video \
  --title-file /tmp/dy_title.txt \
  --content-file /tmp/dy_content.txt \
  --video-file "/abs/path/video.mp4"
uv run python scripts/cli.py click-publish

# 8. 发布图文（分步）
uv run python scripts/cli.py fill-publish-image \
  --title-file /tmp/dy_title.txt \
  --content-file /tmp/dy_content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"
uv run python scripts/cli.py click-publish

# 9. 搜索素材
uv run python scripts/cli.py material-search --query "春天樱花" --media-type image

# 10. 浏览器操作完成后关闭 tab
uv run python scripts/cli.py close-browser
```

## 为何必须使用本技能（而非 browseruse）

抖音有较强的反爬与风控。使用 browseruse 等通用浏览器技能会导致：

- 登录状态无法持久化（每次新开浏览器）
- 易触发风控（缺少 stealth 注入）
- 选择器易因改版失效

本技能集针对抖音做了专门适配，请勿用 browseruse 替代。

| 维度       | store-douyin            | browseruse           |
| ---------- | ----------------------- | -------------------- |
| 登录持久化 | Chrome Profile + Cookie | 每次新会话，无持久化 |
| 反检测     | stealth.js 注入 + CDP   | 通用模式，易被识别   |
| 选择器     | 集中维护，改版只改一处  | 需现场探索，易碎     |
| 多账号     | `--account` 隔离        | 无原生支持           |

## 环境依赖

- **字幕/转录**：依赖 `imageio-ffmpeg`（pyproject.toml 已声明），随 `uv sync` 安装，无需额外声明。
- **字幕/转录失败时**：执行 `uv sync`。

## 失败处理

- **未登录**：提示用户执行登录流程（douyin-auth）。
- **Chrome 未启动**：使用 `chrome_launcher.py` 或确保 Chrome 在调试端口运行。
- **操作超时**：检查网络连接，适当增加等待时间。
- **频率限制**：降低操作频率，增大间隔。
- **浏览器 tab 未关闭**：若忘记调用 `close-browser`，浏览器 tab 会保持打开。
