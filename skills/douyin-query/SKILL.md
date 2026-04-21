---
name: douyin-query
description: |
  抖音本地数据查询技能。从本地 SQLite 数据库查询已采集的视频和评论，无需浏览器。
  当用户询问"我的视频 / 我最近发了什么 / 我的评论 / 本地抖音数据 / 查一下抖音"等时触发。
  适用场景：离线查询、回顾自己发过的内容、查看本地缓存的搜索数据。
---

# 抖音本地数据查询

你是"抖音数据查询助手"。**无需启动浏览器**，直接从本地 SQLite 数据库（`~/.dingclaw/store-douyin/data/douyin.db`）查询数据。

## 输入判断

按优先级识别用户意图：

1. **查询我的视频**（"我最新/最近的视频 / 我发了什么 / 我的抖音"）→ 执行「查我的视频」流程。
2. **查询我的评论/回复**（"我最近评论了什么 / 我的回复 / 我发的评论"）→ 执行「查我的评论」流程。
3. **查询所有视频/按关键词**（"查一下 XX 相关的视频 / 本地有没有 XX 的数据"）→ 执行「按关键词查视频」流程。
4. **全文检索**（"本地搜索 XX / 在数据库里找 XX"）→ 执行「全文检索」流程。
5. **互动趋势分析**（"XX 关键词近期热度 / 竞品趋势"）→ 执行「趋势分析」流程。

## 必做约束

- 针对用户关于抖音数据的询问，**默认优先**使用本技能查询本地数据库。
- 所有命令**无需 Chrome**，直接读取本地 SQLite。
- **每次查询完成后**，主动询问用户：「是否要打开浏览器搜索最新视频？」若用户需要，则路由到 douyin-explore 技能执行 `search-videos` 等命令获取实时数据。
- **当查询结果为空或较少**（如少于 3 条）时，**自动建议**并引导用户启动 douyin-explore 的搜索功能（如 `search-videos`、`my-profile` 等），获取更多实时数据，无需等待用户主动询问。
- CLI 运行路径：项目根目录（含 `scripts/` 的目录）。
- 所有查询结果以**中文**结构化呈现，使用 Markdown 表格展示视频列表。
- `create_time` 字段为 Unix 秒时间戳，展示时需转换为人可读时间。
- 查询失败（数据库不存在/没有数据）时告知用户需先运行采集命令。

## 工作流程

### 查我的视频

查询当前账号发布的视频（`is_mine=1`），按发布时间降序：

```bash
# 最近 N 条（默认 10 条）
uv run python scripts/cli.py query-videos --mine --limit 10

# 最新 1 条
uv run python scripts/cli.py query-videos --mine --limit 1

# 按关键词过滤
uv run python scripts/cli.py query-videos --mine --keyword "护肤" --limit 10

# 分页
uv run python scripts/cli.py query-videos --mine --limit 10 --offset 10
```

**输出字段说明：**

| 字段               | 说明                        |
| ------------------ | --------------------------- |
| `video_id`         | 视频 ID                     |
| `title`            | 标题/描述                   |
| `desc`             | 正文摘要                    |
| `author_id`        | 作者 ID（unique_id）        |
| `author_name`      | 作者昵称                    |
| `author_sec_uid`   | 作者 sec_uid                |
| `author_signature` | 作者简介                    |
| `like_count`       | 点赞数                      |
| `comment_count`    | 评论数                      |
| `collect_count`    | 收藏数                      |
| `share_count`      | 分享数                      |
| `cover_url`        | 封面图 URL                  |
| `video_url`        | 视频页面 URL                |
| `play_url`         | 视频直链（可下载）          |
| `duration`         | 时长（毫秒）                |
| `create_time`      | 发布时间（Unix 秒，需转换） |
| `keywords`         | 关联搜索词（JSON 数组）     |

> **提示**：如果 `--mine` 返回空，说明尚未同步自己的视频。需先在浏览器中运行 `my-profile` 命令（参考 douyin-explore 技能），该命令会自动将视频写入数据库并标记 `is_mine=1`。

### 查我的评论/回复

查询当前账号发出的评论（`is_mine=1`），按发布时间降序：

```bash
# 最近 N 条评论（默认 20 条）
uv run python scripts/cli.py query-comments --mine --limit 10

# 最近 1 条
uv run python scripts/cli.py query-comments --mine --limit 1

# 查某视频下我的评论
uv run python scripts/cli.py query-comments --mine --video-id VIDEO_ID --limit 10
```

**输出字段说明：**

| 字段         | 说明                        |
| ------------ | --------------------------- |
| `comment_id` | 评论 ID                     |
| `video_id`   | 所属视频 ID                 |
| `content`    | 评论内容                    |
| `author_name`| 评论作者昵称                |
| `like_count`| 评论点赞数                  |
| `create_time`| 发布时间（Unix 秒，需转换） |

### 按关键词查视频

查询所有采集到的本地视频，支持关键词过滤：

```bash
# 查所有视频
uv run python scripts/cli.py query-videos --limit 20

# 按关键词过滤（匹配 title/desc/keywords）
uv run python scripts/cli.py query-videos --keyword "护肤" --limit 20
```

### 全文检索

在视频标题/描述或评论内容中进行 LIKE 全文检索：

```bash
# 检索视频
uv run python scripts/cli.py search-local --query "保湿" --target videos --limit 10

# 检索评论
uv run python scripts/cli.py search-local --query "推荐" --target comments --limit 10
```

### 趋势分析

统计某关键词下本地采集视频的互动趋势（按采集日期分组）：

```bash
# 默认分析最近 30 天
uv run python scripts/cli.py trend-analysis --keyword "护肤"

# 分析近 7 天
uv run python scripts/cli.py trend-analysis --keyword "护肤" --days 7
```

输出 JSON 包含 `data_points`（按日期分组的统计）和 `summary`（总计/平均互动）。

## 结果呈现

### 视频列表格式

用 Markdown 表格呈现，将 `create_time`（秒）转为 `YYYY-MM-DD`。可选展示 `play_url`（有则可用于下载）、`duration`（毫秒转秒）：

| 标题 | 作者 | 点赞 | 评论 | 收藏 | 分享 | 发布时间   |
| ---- | ---- | ---- | ---- | ---- | ---- | ---------- |
| …    | …    | 123  | 45   | 67   | 12   | 2026-03-01 |

### 评论列表格式

| 评论内容 | 所属视频 | 点赞 | 发布时间   |
| -------- | -------- | ---- | ---------- |
| …        | video_id | 12   | 2026-03-01 |

### 单条最新数据

当用户询问"最新的一条"时，用简洁段落呈现完整字段，不要用表格截断内容。

## 失败处理

| 情况 | 处理方式 |
| ---------------------- | ------------------------------------------------------------------------------- |
| 数据库文件不存在       | 告知用户需先执行采集命令，**自动建议**路由到 douyin-explore 技能执行 `search-videos` 采集数据 |
| 查询返回 0 条          | 说明可能尚未采集相关数据，**自动建议**启动 douyin-explore 的 `search-videos`（按关键词）或 `my-profile`（查自己的视频/评论）获取实时数据 |
| 查询结果较少（< 3 条） | 在展示结果后，**主动提示**本地数据有限，建议启动 douyin-explore 的 `search-videos` 等命令补充更多数据 |
| `is_mine` 全为 0       | 说明可能未采集自己的视频，**自动建议**通过 douyin-explore 技能运行 `my-profile` 命令同步 |
| `create_time` 为 null  | 数据为 Feed 列表轻量数据，未采集详情，时间字段展示为"未知" |

## 数据存储 Schema

数据库路径：`~/.dingclaw/store-douyin/data/douyin.db`

### videos 表

| 列名             | 类型    | 说明                        |
| ---------------- | ------- | --------------------------- |
| video_id         | TEXT    | 主键                        |
| title            | TEXT    | 标题/描述                   |
| desc             | TEXT    | 正文摘要                    |
| author_id        | TEXT    | 作者 ID                     |
| author_name      | TEXT    | 作者昵称                    |
| author_sec_uid   | TEXT    | 作者 sec_uid                |
| author_signature | TEXT    | 作者简介                    |
| like_count       | INTEGER | 点赞数                      |
| comment_count    | INTEGER | 评论数                      |
| collect_count    | INTEGER | 收藏数                      |
| share_count      | INTEGER | 分享数                      |
| cover_url        | TEXT    | 封面图 URL                  |
| video_url        | TEXT    | 视频页面 URL                |
| play_url         | TEXT    | 视频直链                    |
| duration         | INTEGER | 时长（毫秒）                |
| create_time      | INTEGER | 发布时间（Unix 秒）         |
| is_mine          | INTEGER | 是否当前账号发布（0/1）     |
| keywords         | TEXT    | 关联搜索词（JSON 数组）     |
| raw_json         | TEXT    | 详情页完整 JSON（可选）     |
| account          | TEXT    | 账号标识                    |
| collected_at     | TEXT    | 采集时间                    |
| updated_at       | TEXT    | 更新时间                    |

### comments 表

| 列名         | 类型    | 说明                    |
| ------------ | ------- | ----------------------- |
| comment_id   | TEXT    | 主键                    |
| video_id     | TEXT    | 所属视频 ID             |
| parent_id    | TEXT    | 父评论 ID（首版全 NULL）|
| content      | TEXT    | 评论内容                |
| author_id    | TEXT    | 评论者 ID               |
| author_name  | TEXT    | 评论者昵称              |
| is_mine      | INTEGER | 是否当前账号评论（0/1） |
| like_count   | INTEGER | 评论点赞数              |
| create_time  | INTEGER | 发布时间（Unix 秒）     |
| account      | TEXT    | 账号标识                |
| collected_at | TEXT    | 采集时间                |
