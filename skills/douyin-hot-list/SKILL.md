---
name: douyin-hot-list
description: |
  抖音热榜数据获取与查询技能。获取实时热搜榜、查询历史热榜、统计热榜趋势。
  当用户要求"抖音热榜 / 热搜 / 今天什么火 / 热门话题 / 热点视频 / 查热榜 / 热榜趋势"等时触发。
  支持实时获取（需浏览器）和本地查询（无需浏览器）两种模式。
execution:
  timeout: 60
---

# 抖音热榜

你是"抖音热榜助手"。帮助用户获取抖音实时热搜榜、查询历史热榜数据、分析热榜趋势。

## 输入判断

按优先级识别用户意图：

1. **获取实时热榜**（"抖音热榜 / 现在什么火 / 今天热搜 / 抓取热榜"）→ 执行 `get-hot-list`（需浏览器）。
2. **查询历史热榜**（"昨天热榜 / 查一下之前的热搜 / 本地热榜"）→ 执行 `query-hot-list`（无需浏览器）。
3. **热榜统计**（"热榜趋势 / 最近几天热榜情况 / 热榜统计"）→ 执行 `hot-list-stats`（无需浏览器）。
4. **抓取日志**（"热榜抓取记录 / 上次什么时候抓的"）→ 执行 `hot-list-logs`（无需浏览器）。

## 必做约束

- `get-hot-list` 需要 Chrome 浏览器运行中且已登录抖音，操作前确认登录状态。
- `query-hot-list`、`hot-list-stats`、`hot-list-logs` **无需浏览器**，直接读取本地数据库。
- 获取实时热榜完成后，应执行 `close-browser` 关闭浏览器。
- 热榜数据自动存储到本地 SQLite 数据库（`~/.dingclaw/store-douyin/data/douyin.db`）。
- 获取热榜时会同时从热点频道 API 获取关联视频（约 10-12 条），自动保存到 `videos` 表。

## 工作流程

### 获取实时热榜（需浏览器）

通过浏览器 CDP 在抖音域名下 fetch 热搜 API，同时获取热点频道关联视频。

```bash
# 获取热榜（默认 50 条话题）
uv run python scripts/cli.py get-hot-list

# 指定数量
uv run python scripts/cli.py get-hot-list --count 20

# 不保存到数据库（仅查看）
uv run python scripts/cli.py get-hot-list --no-save
```

**返回数据：**

```json
{
  "success": true,
  "count": 50,
  "topics": [
    {
      "rank": 1,
      "word": "热搜话题标题",
      "hot_value": 11730065,
      "position": 1,
      "cover_url": "https://p3-sign.douyinpic.com/...",
      "word_type": 1,
      "group_id": "7618742647842608424",
      "sentence_id": "2439267",
      "sentence_tag": 5000,
      "event_time": 1774144409,
      "video_count": 3,
      "discuss_video_count": 1,
      "label": 3,
      "related_words": "[]",
      "search_url": "https://www.douyin.com/search/...",
      "fetch_time": "2026-03-22T06:40:32+00:00"
    }
  ],
  "related_videos": 11,
  "saved": {
    "found": 50,
    "new": 50,
    "errors": 0,
    "video_count": 11
  }
}
```

**话题字段说明：**

| 字段 | 说明 |
|---|---|
| `rank` | 排名 |
| `word` | 热搜词 |
| `hot_value` | 热度值 |
| `cover_url` | 封面图 URL |
| `word_type` | 话题类型（1=普通） |
| `label` | 标签（0=普通, 1=新, 3=热, 16=辟谣） |
| `video_count` | 相关视频数 |
| `event_time` | 事件时间（Unix 秒） |
| `search_url` | 搜索链接 |

**关联视频**：自动从热点频道 API 获取约 10-12 条热点视频，数据结构与 `search-videos` 完全一致（含 `video_id`、`title`、`author`、互动数据、`cover_url`、`play_url`、`duration` 等），保存到 `videos` 表，`keywords` 标记为 `["热榜关联"]`。

### 查询历史热榜（无需浏览器）

从本地数据库查询已采集的热榜话题。

```bash
# 查询最新一次热榜
uv run python scripts/cli.py query-hot-list

# 查询指定日期
uv run python scripts/cli.py query-hot-list --date 2026-03-22

# 限制数量
uv run python scripts/cli.py query-hot-list --limit 10
```

### 热榜统计（无需浏览器）

统计最近 N 天的热榜采集情况。

```bash
# 默认统计最近 7 天
uv run python scripts/cli.py hot-list-stats

# 统计最近 30 天
uv run python scripts/cli.py hot-list-stats --days 30
```

### 抓取日志（无需浏览器）

查看热榜抓取历史记录。

```bash
# 最近 10 条日志
uv run python scripts/cli.py hot-list-logs

# 最近 20 条
uv run python scripts/cli.py hot-list-logs --limit 20
```

## 结果呈现

### 热榜列表格式

用 Markdown 表格呈现，`event_time`（秒）转为人可读时间：

| 排名 | 热搜词 | 热度 | 标签 | 视频数 | 时间 |
|------|--------|------|------|--------|------|
| 1 | 湖人胜魔术迎9连胜 | 1173万 | 🔥热 | 3 | 03-22 14:40 |
| 2 | 今天是世界水日 | 1136万 | 🔥热 | 2 | 03-22 14:09 |

**标签映射**：`0`=普通、`1`=🆕新、`3`=🔥热、`16`=✅辟谣。

### 关联视频格式

获取热榜后，如有关联视频，用表格展示前 5 条：

| 标题 | 作者 | 点赞 | 评论 | 时长 |
|------|------|------|------|------|
| … | 人民日报 | 37.7万 | 1.9万 | 19s |

## 完成后关闭浏览器

`get-hot-list` 执行完毕后：

```bash
uv run python scripts/cli.py close-browser
```

## 数据存储

### hot_topics 表

| 列名 | 类型 | 说明 |
|---|---|---|
| id | TEXT | 主键（日期+排名+word哈希） |
| rank | INTEGER | 排名 |
| word | TEXT | 热搜词 |
| hot_value | INTEGER | 热度值 |
| position | INTEGER | 位置 |
| cover_url | TEXT | 封面图 |
| word_type | INTEGER | 话题类型 |
| group_id | TEXT | 分组 ID |
| sentence_id | TEXT | 句子 ID |
| sentence_tag | INTEGER | 句子标签 |
| event_time | INTEGER | 事件时间 |
| video_count | INTEGER | 视频数 |
| discuss_video_count | INTEGER | 讨论视频数 |
| label | INTEGER | 标签 |
| related_words | TEXT | 相关词（JSON） |
| search_url | TEXT | 搜索链接 |
| fetch_time | TEXT | 采集时间 |

### hot_fetch_logs 表

| 列名 | 类型 | 说明 |
|---|---|---|
| id | INTEGER | 自增主键 |
| found | INTEGER | 发现话题数 |
| new_count | INTEGER | 新增话题数 |
| video_count | INTEGER | 关联视频数 |
| status | INTEGER | 状态（0=成功, 1=部分失败, 2=失败） |
| error | TEXT | 错误信息 |
| started_at | TEXT | 开始时间 |
| finished_at | TEXT | 完成时间 |

## 失败处理

| 错误 | 处理方式 |
|---|---|
| 未登录 / 浏览器未启动 | 提示先执行登录（参考 douyin-auth 技能） |
| 热榜 API 返回空 | 重试 2 次，降级到 DOM 提取 |
| 关联视频获取失败 | 不影响话题获取，仅日志警告 |
| 本地无数据 | 建议先执行 `get-hot-list` 获取实时数据 |
| 数据库不存在 | 首次运行会自动创建 |
