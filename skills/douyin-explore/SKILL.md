---
name: douyin-explore
description: |
  抖音内容发现与数据采集技能。搜索视频、查看详情、获取用户资料。
  当用户要求搜索视频、查看详情、查看用户主页、采集/搜集抖音视频数据时触发。
execution:
  timeout: 300
---

# 抖音内容发现与数据采集

你是"抖音发现助手"。帮助用户搜索视频、查看详情、获取用户资料。采集流程：search-videos 获取数据并保存 JSON，报告由 store-insight-report 技能生成。

## 输入判断

按优先级判断：

1. 用户要求"搜集/采集关于 xxx 的抖音视频"：执行 **数据采集** 流程。
2. 用户要求"搜索视频 / 找视频"：执行搜索流程。
3. 用户要求"查看详情 / 视频详情"：执行获取详情流程。
4. 用户要求"查看用户 / 用户主页"：执行获取用户资料流程。
5. 用户要求"我的主页"：执行获取当前用户主页流程。

## 必做约束

- 所有操作前应确认登录状态（Chrome 需在运行中且已登录）。
- **采集/探索操作全部完成后**，应执行 `close-browser` 关闭浏览器（见文末「完成后关闭浏览器」）。
- 采集的数据会自动存储到本地数据库。
- 文件路径使用绝对路径。
- **字幕/转录**：依赖 `imageio-ffmpeg`（pyproject.toml 已声明），随 `uv sync` 安装。
- **技能原子性**：search-videos 仅返回列表，不获取详情；需详情时由模型调用 get-video-detail（与小红书 search-feeds / get-feed-detail 设计一致）。
- **`search-videos` 数量**：
  - CLI **默认 `--count=20`**（命令里不写 `--count` 即采集 **20 条**列表）。
  - 用户说了具体数量（如「搜 30 条」）必须传 **`--count 30`**，**禁止**擅自改成更小值。

### 超时与默认参数（执行时须遵守）

- **命令超时**：本技能下所有 CLI 命令执行时 `timeout=300`（秒），低于此值易因详情补充导致超时。
- **user-profile / my-profile 默认仅列表**：执行时**必须**带 `--no-enrich-details`，仅获取列表；仅在用户明确要求「要详情/要字幕/要评论」时，可去掉该参数或对部分视频单独调用 get-video-detail。
- **获取列表的正确命令**：
  ```bash
  uv run python scripts/cli.py my-profile --no-enrich-details
  uv run python scripts/cli.py user-profile --user-id SEC_UID --no-enrich-details
  ```
- **需详情时**：先用 `--no-enrich-details` 拿列表，再对需要的 video_id 逐条调用 get-video-detail，每条间隔 3.0～5.0 秒。

## 工作流程

### 搜索视频

**仅返回搜索列表**，不获取详情。搜索 API 已返回：标题、作者、互动数、封面、**play_url**（视频直链）、duration 等，无需进详情页即可下载。需评论、字幕、转录时，由模型调用 get-video-detail。

#### ⚠️ 搜索反爬虫约束（必须遵守）

`search-videos` 内部采用**模拟真实用户搜索**的方式执行：先导航到抖音首页 → 定位搜索框 → 逐字输入关键词 → 按回车/点击搜索按钮触发搜索。**禁止**通过直接导航到搜索 URL（如 `douyin.com/search/xxx?type=video`）的方式执行搜索，这会触发抖音的反爬虫验证码。此逻辑已在 CLI 内部实现，无需额外操作，但**禁止**绕过 CLI 自行拼接搜索 URL 进行导航。

#### 数据完整性（与默认 count）

- 与上文「必做约束」一致：**默认 20 条**、用户指定须 **`--count N`**、**不得**用「只 get 5 条详情」当借口少搜列表。
- **交付**：结束时用一句话写明实际条数，例如「已完成 **N** 条视频采集（关键词 xxx）」。
- **大批量（>20）**：需分批时按批汇报进度；承诺 N 条则要跑完计划或说明平台不足，避免只跑一批就交差。

```bash
# 搜索视频（默认 20 条）
uv run python scripts/cli.py search-videos --keyword "关键词"

# 用户说「搜 30 条」时必须：
uv run python scripts/cli.py search-videos --keyword "关键词" --count 30
# 若单次 API/超时限制，则分两批：先 --count 20，再 --count 10（或换关键词/翻页策略按子技能说明），并汇报累计条数
```

支持的排序方式：

- `--sort-by ""` 综合（默认）
- `--sort-by "latest"` 最新
- `--sort-by "most_liked"` 最多点赞

### 获取视频详情

从 search-videos 结果中取 `video_id`，获取完整详情（评论、字幕、转录）。**默认补充字幕**（API subtitle_infos + 必剪 ASR）和**转录**（必剪 ASR 优先，Whisper 兜底；视频时长≤16s 时 Whisper 可用）。先取 video_info 判断时长，>16s 不下载视频，避免浪费。可用 `--no-fetch-subtitles` / `--no-fetch-transcript` 跳过。

```bash
# 默认：视频信息 + 评论 + 字幕 + 转录（必剪 ASR 优先，Whisper 兜底）
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID --max-comments 50

# 已知是笔记（图文）时，加 --aweme-type note：直接用 /note/ URL 定位，跳过字幕和转录，返回图片列表
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID --aweme-type note --max-comments 50

# 仅验证详情页数据（跳过字幕和转录）
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID --no-fetch-subtitles --no-fetch-transcript

# 跳过转录（保留字幕）
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID --no-fetch-transcript

# 指定 Whisper 兜底模型（base/tiny/small，默认 base；仅必剪 ASR 失败时使用）
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID --whisper-model small
```

> **笔记识别规则**：
>
> - 用户提供的链接包含 `/note/`，或 search-videos 返回的 `aweme_type == 68`，或用户明确说"笔记"→ 加 `--aweme-type note`
> - 不确定类型时可不传 `--aweme-type`，CLI 会从 API 返回的 `aweme_type` 自动识别并切换逻辑

返回字段：`video`、`comments`、`subtitle`、`transcript`（执行转录时）。

#### 分批获取详情（必须遵守）

当用户要求获取视频/笔记的详情数据时，逐条执行，每条间隔 3.0～5.0 秒。

**执行流程**：

1. **先获取列表**：用 `search-videos` 或 `my-profile --no-enrich-details` 拿到完整列表。
2. **逐条执行**：按用户要求的数量依次调用 get-video-detail，每条间隔 3.0～5.0 秒。
3. **汇总结果**：全部完成后，汇总所有详情数据。

**执行命令示例**：

```bash
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID_1 --max-comments 50
# 间隔  3.0～5.0 秒
uv run python scripts/cli.py get-video-detail --video-id VIDEO_ID_2 --max-comments 50
# ...依次执行
```

**注意事项**：

- **列表要先采全**：`search-videos --count` 必须与用户约定一致，不得因获取详情而减少列表条数。
- **失败处理**：某条详情获取失败时，记录警告并跳过，继续处理剩余条目。
- **用户未指定数量时**：默认对前 5 条获取详情。

### 获取用户主页

**默认行为（必做约束）**：必须使用 `--no-enrich-details` 仅获取列表，避免超时。用户明确要详情时，再对部分视频调用 get-video-detail。

```bash
# 当前登录账号（仅列表，默认写法）
uv run python scripts/cli.py my-profile --no-enrich-details

# 指定用户（仅列表）
uv run python scripts/cli.py user-profile --user-id SEC_UID --no-enrich-details

# 需详情时：去掉 --no-enrich-details（前 5 条补充详情，耗时更长）
uv run python scripts/cli.py my-profile

# 需要更多列表：先 --no-enrich-details，再对关键视频 get-video-detail
uv run python scripts/cli.py my-profile --no-enrich-details --max-videos 30

# 控制详情补充数量（默认 5；需更多可调高，但易超时）
uv run python scripts/cli.py my-profile --max-enrich 8

# 跳过转录（保留字幕，更快）
uv run python scripts/cli.py my-profile --no-fetch-transcript
```

### 超时与分批

**单次 skill 约 2 分钟限制**。CLI 已做优化避免超时：

| 场景                      | 默认行为                     | 超时防护             |
| ------------------------- | ---------------------------- | -------------------- |
| user-profile / my-profile | 列表 + `--no-enrich-details` | 仅列表，秒级完成     |
| search-videos             | 返回列表，不获取详情         | 默认 20 条，秒级完成 |
| get-video-detail          | 每条约 15～25s，按需逐条执行 | 每条间隔 3.0～5.0 秒 |

1. **列表与详情分离**：先用 `search-videos` 或 `my-profile --no-enrich-details` 拿全量列表，再按用户要求逐条调用 `get-video-detail`。
2. **每条间隔**：每次调用之间间隔 3.0～5.0 秒，避免触发风控。
3. **单条失败**：跳过继续，见失败处理表。
4. **用户未指定详情数量时**：默认对前 5 条获取详情。

### 数据采集（搜索 + 保存 JSON）

采集流程：`search-videos` 获取数据 → 将输出保存为 JSON 文件 → 将文件路径传给 **store-insight-report** 技能生成分析报告。

#### 采集步骤

1. 执行搜索，将 JSON 输出重定向到文件（确保目录存在：`mkdir -p ~/.dingclaw/store-douyin/data`）。**`--count` 必须与用户约定一致**；超过 20 条时分批执行并汇报。

```bash
uv run python scripts/cli.py search-videos --keyword "春招" --count 30 \
  > ~/.dingclaw/store-douyin/data/douyin_search_春招_$(date +%Y%m%d_%H%M%S).json
```

2. 采集完成后，**调用 store-insight-report 技能**，传入上述 JSON 文件路径，由该技能生成分析报告。不要在本技能内输出报告内容。

#### 可选：需详情时

若需评论、字幕、转录，按用户要求的数量逐条调用 `get-video-detail`（每次间隔 3.0～5.0 秒），再合并数据。

### 输出 Schema（与 store-insight-report 兼容）

search-videos 返回的 `videos` 数组，每项结构：

```json
{
  "video_id": "7599692006406786358",
  "title": "视频标题",
  "author": "作者昵称",
  "author_id": "elvinpancl",
  "author_sec_uid": "MS4wLjABAAAALMki6_...",
  "author_signature": "作者简介",
  "like_count": 282823,
  "comment_count": 4555,
  "share_count": 60873,
  "collect_count": 201690,
  "url": "https://www.douyin.com/video/7599692006406786358",
  "cover_url": "https://p3-pc-sign.douyinpic.com/...",
  "play_url": "https://v26-web.douyinvod.com/...",
  "duration": 25667,
  "create_time": 1769441196
}
```

转 StoreCollection 时：`id`←video_id，`author`←{userId: author_id, nickname: author}，`interact`←{likedCount, collectedCount, commentCount, sharedCount}，`coverUrl`←cover_url，`publishTime`←create_time，`platformExtras`←{playUrl, duration, authorSignature}。

## 数据存储

采集的数据会自动存储到本地 SQLite 数据库：

- 数据库路径：`~/.dingclaw/store-douyin/data/douyin.db`
- 视频表：`videos`
- 评论表：`comments`

## 完成后关闭浏览器

所有采集、探索操作（search-videos、get-video-detail、user-profile、my-profile）执行完毕后，应执行：

```bash
uv run python scripts/cli.py close-browser
```

## 失败处理

| 错误               | 处理方式                                                       |
| ------------------ | -------------------------------------------------------------- |
| 未登录             | 提示先执行登录                                                 |
| 搜索结果为空       | 尝试更换关键词                                                 |
| 视频不可访问       | 可能是私密或已删除视频                                         |
| 用户不存在         | 检查用户 ID 是否正确                                           |
| 单篇 enrich 失败   | 警告日志，跳过该条继续                                         |
| 数据量过大导致超时 | 用 `--no-enrich-details` 仅列表；或减 `--max-enrich`（默认 5） |
| 字幕/转录失败      | 执行 `uv sync`                                                 |
