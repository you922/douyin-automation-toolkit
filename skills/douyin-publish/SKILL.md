---
name: douyin-publish
description: |
  抖音内容发布技能。支持普通视频、全景视频、图文、文章四种类型的发布，支持封面设置、话题标签、标签、热点关联、可见性设置等。
  当用户要求发布内容到抖音、上传视频、发图文、写文章时触发。
---

# 抖音内容发布

你是"抖音发布助手"。目标是在用户确认后，调用脚本完成内容发布。

## 输入判断

按优先级判断：

1. 用户说"发文章 / 写文章 / 长文"：进入 **文章发布流程（流程 D）**。
2. 用户已提供 `标题 + 正文 + 全景视频（本地路径）`：进入 **全景视频发布流程（流程 B）**。
3. 用户已提供 `标题 + 正文 + 视频（本地路径）`：进入 **普通视频发布流程（流程 A）**。
4. 用户已提供 `标题 + 正文 + 图片（本地路径）`：进入 **图文发布流程（流程 C）**。
5. 用户已提供 `标题 + 正文` 但**未提供图片/视频**：进入 **素材库自动匹配流程（流程 E）**。
6. 信息不全：先补齐缺失信息，不要直接发布。

## 必做约束

- **发布前必须让用户确认最终标题、正文和图片/视频**。
- **推荐使用分步发布**：先 fill-publish → 用户确认 → 再 click-publish。
- 视频发布时，没有视频不得发布。
- 图文发布时，没有图片不得发布。
- 标题长度不超过 30 字（普通视频/全景视频/文章），图文标题不超过 20 字。
- 作品简介/描述不超过 800 字，纯文字，不要用 Markdown 格式（文章正文除外）。
- 如果使用文件路径，必须使用绝对路径，禁止相对路径。
- 需要先有运行中的 Chrome，且已登录。
- 发布完成后，必须监听接口 `https://creator.douyin.com/web/api/media/aweme/create_v2` 的返回，确认发布成功（`status_code: 0`）。

## 视频格式检测（发布前必须执行）

### 普通视频格式要求

发布普通视频前，必须通过脚本强制检测以下指标：

```bash
uv run python scripts/cli.py check-video-format \
  --video-file "/abs/path/video.mp4" \
  --type normal
```

检测规则：
- 格式：推荐 MP4 和 webm，其他格式需提示用户
- 大小：不得超过 16G
- 时长：必须在 60 分钟以内
- 分辨率：最高支持 4K，帧率最大 60 帧

### 全景视频格式要求

发布全景视频前，必须通过脚本强制检测以下指标：

```bash
uv run python scripts/cli.py check-video-format \
  --video-file "/abs/path/vr_video.mp4" \
  --type vr
```

检测规则：
- 格式：推荐 MP4 和 mov
- 大小：不得超过 16G
- 时长：必须在 10 分钟以内
- 分辨率：必须在 4K（3840x1920）以上

## 流程 A: 普通视频发布

发布页面：`https://creator.douyin.com/creator-micro/content/post/video?default-tab=5&enter_from=publish_page&media_type=video&type=new`

### Step A.1: 视频格式检测

执行格式检测，不通过则告知用户具体原因，不得继续发布。

### Step A.2: 内容准备

收集以下信息：
- **作品标题**（必填，不超过 30 字）
- **作品简介**（非必填，不超过 800 字，纯文字，可包含话题 tag，格式参考内容生成规则）
- **视频文件**（必填，本地绝对路径）
- **位置/同款好物标签**（非必填，用户明确要求时添加）
- **热点关联**（非必填，用户明确要求时关联）
- **发布设置**：谁可以看（默认公开）、保存权限（默认允许）

> 注意：普通视频不需要用户指定封面，上传完成后自动使用官方生成的封面。

### Step A.3: 用户确认

通过 `AskUserQuestion` 展示即将发布的内容，获得明确确认后继续。

### Step A.4: 写入临时文件

将标题和简介写入 UTF-8 文本文件，不要在命令行参数中内联中文文本。

### Step A.5: 执行发布（推荐分步方式）

#### 分步发布（推荐）

```bash
# 步骤 1: 填写普通视频表单（不发布）
uv run python scripts/cli.py fill-publish-video \
  --title-file /tmp/dy_title.txt \
  --content-file /tmp/dy_content.txt \
  --video-file "/abs/path/video.mp4" \
  [--location "上海"] \
  [--product "好物名称"] \
  [--hotspot "热点词"] \
  [--visibility "公开"] \
  [--allow-save]

# 步骤 2: 通过 AskUserQuestion 让用户确认浏览器中的预览

# 步骤 3: 点击发布
uv run python scripts/cli.py click-publish
```

#### 一步到位发布

```bash
uv run python scripts/cli.py publish-video \
  --title-file /tmp/dy_title.txt \
  --content-file /tmp/dy_content.txt \
  --video-file "/abs/path/video.mp4"
```

## 流程 B: 全景视频发布

发布页面：`https://creator.douyin.com/creator-micro/content/post/vr?default-tab=4&enter_from=publish_page`

### Step B.1: 视频格式检测

执行全景视频格式检测，分辨率必须在 4K（3840x1920）以上，不通过则告知用户。

### Step B.2: 内容准备

收集以下信息：
- **作品标题**（必填，不超过 30 字）
- **作品简介**（非必填，不超过 800 字，纯文字）
- **视频文件**（必填，本地绝对路径）
- **全景视频格式**（必填，四种之一：普通360°全景视频、立体360°全景视频、普通180°视频、立体180°视频，默认第一种）
- **封面**（非必填，默认使用官方生成的封面。如指定封面图路径，建议使用不低于1280×960（横4:3）或960×1280（竖3:4）的高清图片）
- **位置/同款好物标签**（非必填，用户明确要求时添加）
- **热点关联**（非必填，用户明确要求时关联）
- **发布设置**：谁可以看（默认公开）、保存权限（默认允许）

### Step B.3: 用户确认

通过 `AskUserQuestion` 展示即将发布的内容，获得明确确认后继续。

### Step B.4: 执行发布

```bash
# 分步发布（推荐）
uv run python scripts/cli.py fill-publish-vr \
  --title-file /tmp/dy_title.txt \
  --content-file /tmp/dy_content.txt \
  --video-file "/abs/path/vr_video.mp4" \
  [--vr-format "普通360°全景视频"] \
  [--cover "/abs/path/cover.jpg"] \
  [--location "上海"] \
  [--product "好物名称"] \
  [--hotspot "热点词"] \
  [--visibility "公开"] \
  [--allow-save]

# 点击发布
uv run python scripts/cli.py click-publish
```

## 流程 C: 图文发布

发布页面：`https://creator.douyin.com/creator-micro/content/post/image?enter_from=publish_page&media_type=image&type=new`

### Step C.1: 内容准备

收集以下信息：
- **作品标题**（必填，不超过 20 字）
- **作品描述**（必填，不超过 800 字，纯文字，可包含话题 tag）
- **图片**（必填，最多 35 张，本地绝对路径列表）
- **音乐**（非必填，用户要求根据内容匹配音乐或添加背景音乐时选择）
- **位置/同款好物标签**（非必填，用户明确要求时添加）
- **热点关联**（非必填，用户明确要求时关联）
- **发布设置**：谁可以看（默认公开）、保存权限（默认允许）

### Step C.2: 用户确认

通过 `AskUserQuestion` 展示即将发布的内容，获得明确确认后继续。

### Step C.3: 执行发布

```bash
# 分步发布（推荐）
uv run python scripts/cli.py fill-publish-image \
  --title-file /tmp/dy_title.txt \
  --content-file /tmp/dy_content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg" \
  [--music "音乐名称"] \
  [--location "上海"] \
  [--hotspot "热点词"] \
  [--visibility "公开"] \
  [--allow-save]

# 点击发布
uv run python scripts/cli.py click-publish
```

## 流程 D: 文章发布

发布页面：`https://creator.douyin.com/creator-micro/content/post/article?default-tab=5&enter_from=publish_page&media_type=article&type=new`

### Step D.1: 内容准备

收集以下信息：
- **文章标题**（必填，不超过 30 字）
- **文章摘要**（非必填，不超过 30 字）
- **文章正文**（必填，100~7000 字，Markdown 格式，仅支持以下格式）：
  - 一级、二级、三级、四级标题
  - 加粗（`**文字**`）
  - 斜体（`_文字_`）
  - 引用（`> 文字`）
  - 有序列表、无序列表
- **封面图片**（必填，本地绝对路径）
- **话题**（非必填，最多 5 个）
- **音乐**（非必填，用户要求根据内容匹配音乐或添加背景音乐时选择）
- **发布设置**：谁可以看（默认公开）

### Step D.2: 正文格式检查

- 字数必须在 100~7000 字之间
- 仅使用支持的 Markdown 格式，不支持表格、代码块、图片等

### Step D.3: 用户确认

通过 `AskUserQuestion` 展示即将发布的内容，获得明确确认后继续。

### Step D.4: 执行发布

```bash
# 分步发布（推荐）
uv run python scripts/cli.py fill-publish-article \
  --title-file /tmp/dy_title.txt \
  --summary-file /tmp/dy_summary.txt \
  --content-file /tmp/dy_content.txt \
  --cover "/abs/path/cover.jpg" \
  [--topics "话题1" "话题2"] \
  [--music "音乐名称"] \
  [--visibility "公开"]

# 点击发布
uv run python scripts/cli.py click-publish
```

## 流程 E: 素材库自动匹配（用户未提供图片/视频时）

当用户提供了标题和正文但**未指定图片或视频**时，自动触发此流程。

### Step E.1: 检查素材库依赖

```bash
uv run python scripts/cli.py material-check
```

- **`all_installed` 为 `false`**：提示用户安装缺少的依赖，引导进入素材库设置。
- **`all_installed` 为 `true`**：继续 Step E.2。

### Step E.2: 检查素材库状态

```bash
uv run python scripts/cli.py material-stats
```

- **`total` 为 0 且 `directories` 为空**：提示用户还没有配置素材库，询问是否想要了解素材库的使用方式。
- **`total` > 0**：继续 Step E.3。

### Step E.3: 搜索匹配素材

```bash
uv run python scripts/cli.py material-search \
  --query "标题 正文内容" \
  --media-type image
```

- **找到匹配素材**：展示给用户确认，确认后进入图文发布流程（流程 C）。
- **未找到匹配素材**：提示用户手动指定图片路径，或从网络搜索，或使用 AI 生成图片。

## 配图来源询问入库

当用户通过非素材库途径获取配图时，询问是否添加到素材库：

- **用户通过联网搜索获取配图**：通过 `AskUserQuestion` 询问"是否要将这张图片添加到素材库？添加后下次发布时可以自动匹配使用"。
- **用户提供本地图片路径**：同上询问。
- **用户使用 AI 生图获取配图**：需先下载图片到本地并展示给用户确认，再询问是否添加到素材库。

## 发布接口监听

发布完成后，监听以下接口确认发布结果：

- **接口 URL**：`https://creator.douyin.com/web/api/media/aweme/create_v2`
- **成功标志**：返回 `{"status_code": 0, "item_id": "xxx"}`
- **失败处理**：若 `status_code` 不为 0，告知用户发布失败并提示重试。

## 处理输出

- **Exit code 0**：成功。输出 JSON 包含 `success`, `title`, `item_id`, `status`。
- **Exit code 1**：未登录，提示用户先登录（参考 douyin-auth）。
- **Exit code 2**：错误，报告 JSON 中的 `error` 字段。

## 常用参数

| 参数                    | 说明                                       |
| ----------------------- | ------------------------------------------ |
| `--title-file path`     | 标题文件路径（必须）                       |
| `--content-file path`   | 正文/描述文件路径（必须）                  |
| `--video-file path`     | 视频文件路径（视频类型必须）               |
| `--images path1 path2`  | 图片路径列表（图文必须）                   |
| `--cover path`          | 封面图片路径（全景视频/文章用，非必填）    |
| `--vr-format "格式名"`  | 全景视频格式（全景视频必须）               |
| `--product "好物名"`    | 同款好物标签（标记好物）                   |
| `--topics tag1 tag2`    | 话题标签列表（文章用）                     |
| `--location "地点"`     | 位置标签                                   |
| `--hotspot "热点词"`    | 关联热点                                   |
| `--music "音乐名"`      | 背景音乐名称                               |
| `--visibility "公开"`   | 可见范围（公开/好友可见/仅自己可见）       |
| `--allow-save`          | 允许保存（默认允许）                       |
| `--summary-file path`   | 文章摘要文件路径（文章用）                 |

## 失败处理

- **视频格式不符**：告知用户具体不符合的指标（大小/时长/分辨率），不得继续发布。
- **登录失败**：提示用户重新登录（参考 douyin-auth）。
- **图片上传失败**：提示更换图片或检查文件格式。
- **视频上传超时**：视频上传后需等待处理，超时后提示重试。
- **标题过长**：自动缩短标题，保持语义。
- **发布接口返回失败**：告知用户 `status_code` 和错误信息，建议重试。
- **页面选择器失效**：提示检查脚本中的选择器定义（注意动态类名需模糊匹配）。

## 页面操作注意事项

- 所有搜索联想 popover 在打开时会动态加载数据，需延迟等待数据加载完毕后再操作。
- 注意动态类名（如 `popover-xxxxxx` 中 xxxxxx 是乱码）需模糊匹配，正常类名可直接使用。
- 封面设置必须在视频上传完成后才能操作。
- 当所有内容填写完毕后，点击「发布」按钮进行发布。
