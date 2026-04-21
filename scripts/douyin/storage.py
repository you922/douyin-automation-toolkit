"""SQLite 持久化存储 — 视频与评论。

存储路径:  ~/.dingclaw/store-douyin/data/douyin.db
身份文件:  ~/.dingclaw/store-douyin/data/me.json（各账号的 author_id，用于 is_mine 标记）

业务表（2张）:
  videos   — 视频（搜索/用户主页轻量数据 + get-video-detail 完整数据）
  comments — 评论（扁平列表，parent_id 全为 NULL，首版无嵌套回复）
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import logging

if TYPE_CHECKING:
    from douyin.types import Feed

logger = logging.getLogger(__name__)
UTC = timezone.utc


# ========== 工具函数 ==========


def _parse_count(s: str | int) -> int:
    """解析互动数量为整数。

    支持: 1234, "1.2w", "3.5万", "999+", "" → 0
    """
    if s is None:
        return 0
    if isinstance(s, int):
        return s
    s = str(s).strip()
    if not s:
        return 0
    try:
        m = re.match(r"^([\d.]+)万$", s)
        if m:
            return int(float(m.group(1)) * 10000)
        if "w" in s.lower() or "万" in s:
            num_part = re.sub(r"[^\d.]", "", s)
            return int(float(num_part) * 10000) if num_part else 0
        if "亿" in s:
            num_part = re.sub(r"[^\d.]", "", s)
            return int(float(num_part) * 100000000) if num_part else 0
        m = re.match(r"^(\d+)", s)
        if m:
            return int(m.group(1))
        return 0
    except (ValueError, TypeError):
        return 0


def _now_iso() -> str:
    """返回当前 UTC 时间 ISO8601 字符串。"""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ========== 主类 ==========


class DYStorage:
    """抖音数据 SQLite 存储。

    多账号通过 account 参数隔离：写入时注入，查询时按 account 过滤。

    使用方式:
        storage = DYStorage(account="default")
        storage.upsert_videos_from_feeds(feeds, keyword="护肤")
        videos = storage.query_videos(keyword="护肤", limit=10)
        storage.close()
    """

    DEFAULT_DB_PATH = Path.home() / ".dingclaw" / "store-douyin" / "data" / "douyin.db"

    def __init__(self, db_path: Path | str | None = None, account: str = "default") -> None:
        self._db_path = Path(db_path) if db_path else self.DEFAULT_DB_PATH
        self._account = account or "default"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def close(self) -> None:
        """关闭数据库连接。"""
        self._conn.close()

    # ========== 内部：建表 ==========

    def _init_db(self) -> None:
        """建表，幂等（CREATE TABLE/INDEX IF NOT EXISTS）。"""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                video_id       TEXT PRIMARY KEY,
                title          TEXT,
                desc           TEXT,
                author_id      TEXT,
                author_name    TEXT,
                author_sec_uid TEXT,
                author_signature TEXT,
                like_count     INTEGER DEFAULT 0,
                comment_count  INTEGER DEFAULT 0,
                collect_count  INTEGER DEFAULT 0,
                share_count    INTEGER DEFAULT 0,
                cover_url      TEXT,
                video_url      TEXT,
                play_url       TEXT,
                duration       INTEGER,
                create_time    INTEGER,
                is_mine        INTEGER DEFAULT 0,
                aweme_type     INTEGER DEFAULT 0,
                images         TEXT,
                keywords       TEXT,
                raw_json       TEXT,
                account        TEXT DEFAULT 'default',
                collected_at   TEXT,
                updated_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_videos_account    ON videos(account);
            CREATE INDEX IF NOT EXISTS idx_videos_is_mine    ON videos(is_mine);
            CREATE INDEX IF NOT EXISTS idx_videos_author_id  ON videos(author_id);
            CREATE INDEX IF NOT EXISTS idx_videos_collected ON videos(collected_at);

            CREATE TABLE IF NOT EXISTS comments (
                comment_id   TEXT PRIMARY KEY,
                video_id     TEXT,
                parent_id    TEXT,
                content      TEXT,
                author_id    TEXT,
                author_name  TEXT,
                is_mine      INTEGER DEFAULT 0,
                like_count   INTEGER DEFAULT 0,
                create_time  INTEGER,
                account      TEXT DEFAULT 'default',
                collected_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_comments_video   ON comments(video_id);
            CREATE INDEX IF NOT EXISTS idx_comments_is_mine ON comments(is_mine);
            CREATE INDEX IF NOT EXISTS idx_comments_author  ON comments(author_id);

            CREATE TABLE IF NOT EXISTS hot_topics (
                id                   TEXT PRIMARY KEY,
                rank                 INTEGER,
                word                 TEXT NOT NULL,
                hot_value            INTEGER DEFAULT 0,
                position             INTEGER DEFAULT 0,
                cover_url            TEXT,
                word_type            INTEGER DEFAULT 0,
                group_id             TEXT,
                sentence_id          TEXT,
                sentence_tag         INTEGER DEFAULT 0,
                event_time           INTEGER DEFAULT 0,
                video_count          INTEGER DEFAULT 0,
                discuss_video_count  INTEGER DEFAULT 0,
                label                INTEGER DEFAULT 0,
                related_words        TEXT,
                search_url           TEXT,
                fetched_at           TEXT,
                fetch_date           TEXT,
                account              TEXT DEFAULT 'default'
            );

            CREATE INDEX IF NOT EXISTS idx_hot_topics_date    ON hot_topics(fetch_date);
            CREATE INDEX IF NOT EXISTS idx_hot_topics_rank    ON hot_topics(rank);
            CREATE INDEX IF NOT EXISTS idx_hot_topics_word    ON hot_topics(word);
            CREATE INDEX IF NOT EXISTS idx_hot_topics_account ON hot_topics(account);

            CREATE TABLE IF NOT EXISTS hot_fetch_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at  TEXT,
                ended_at    TEXT,
                found       INTEGER DEFAULT 0,
                new_count   INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                status      INTEGER DEFAULT 0,
                error       TEXT,
                account     TEXT DEFAULT 'default'
            );

            CREATE INDEX IF NOT EXISTS idx_hot_fetch_logs_started ON hot_fetch_logs(started_at);
        """)
        self._migrate_videos_columns()
        self._migrate_hot_topics_table()

    def _migrate_hot_topics_table(self) -> None:
        """为已有数据库添加 hot_topics 和 hot_fetch_logs 表（幂等）。"""
        for sql in (
            """CREATE TABLE IF NOT EXISTS hot_topics (
                id TEXT PRIMARY KEY, rank INTEGER, word TEXT NOT NULL,
                hot_value INTEGER DEFAULT 0, position INTEGER DEFAULT 0,
                cover_url TEXT, word_type INTEGER DEFAULT 0, group_id TEXT,
                sentence_id TEXT, sentence_tag INTEGER DEFAULT 0,
                event_time INTEGER DEFAULT 0, video_count INTEGER DEFAULT 0,
                discuss_video_count INTEGER DEFAULT 0, label INTEGER DEFAULT 0,
                related_words TEXT, search_url TEXT, fetched_at TEXT,
                fetch_date TEXT, account TEXT DEFAULT 'default')""",
            """CREATE TABLE IF NOT EXISTS hot_fetch_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT,
                ended_at TEXT, found INTEGER DEFAULT 0, new_count INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0, status INTEGER DEFAULT 0,
                error TEXT, account TEXT DEFAULT 'default')""",
            "CREATE INDEX IF NOT EXISTS idx_hot_topics_date ON hot_topics(fetch_date)",
            "CREATE INDEX IF NOT EXISTS idx_hot_topics_rank ON hot_topics(rank)",
            "CREATE INDEX IF NOT EXISTS idx_hot_topics_word ON hot_topics(word)",
            "CREATE INDEX IF NOT EXISTS idx_hot_topics_account ON hot_topics(account)",
            "CREATE INDEX IF NOT EXISTS idx_hot_fetch_logs_started ON hot_fetch_logs(started_at)",
        ):
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        self._conn.commit()

    def _migrate_videos_columns(self) -> None:
        """为已有 videos 表添加新列、移除废弃列（幂等）。

        列定义格式：
        - 仅列名（如 "author_sec_uid"）→ 默认类型 TEXT
        - 完整定义（如 "aweme_type INTEGER DEFAULT 0"）→ 原样使用
        """
        columns_to_add = (
            "author_sec_uid TEXT",
            "author_signature TEXT",
            "aweme_type INTEGER DEFAULT 0",
            "images TEXT",
        )
        for col_def in columns_to_add:
            try:
                self._conn.execute(f"ALTER TABLE videos ADD COLUMN {col_def}")
                self._conn.commit()
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        # 移除 play_count（接口不返回，SQLite 3.35+ 支持 DROP COLUMN）
        try:
            self._conn.execute("ALTER TABLE videos DROP COLUMN play_count")
            self._conn.commit()
        except sqlite3.OperationalError:
            pass  # 无此列或旧版 SQLite 不支持 DROP COLUMN，忽略
        self._conn.commit()

    # ========== 内部：身份文件（me.json sidecar）==========

    def _me_file(self) -> Path:
        return self._db_path.parent / "me.json"

    def set_my_identity(self, author_id: str) -> None:
        """保存当前账号的 author_id，供后续评论 is_mine 标记使用。"""
        if not author_id:
            return
        me_file = self._me_file()
        data: dict = {}
        if me_file.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                data = json.loads(me_file.read_text(encoding="utf-8"))
        data[self._account] = author_id
        me_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_my_author_id(self) -> str:
        """读取当前账号的 author_id（未知则返回空字符串）。"""
        me_file = self._me_file()
        if not me_file.exists():
            return ""
        try:
            data = json.loads(me_file.read_text(encoding="utf-8"))
            return data.get(self._account, "")
        except (json.JSONDecodeError, OSError):
            return ""

    # ========== 内部：工具方法 ==========

    @staticmethod
    def _merge_keyword(existing_json: str | None, keyword: str | None) -> str:
        """将新关键词合并进现有 JSON 数组（去重）。"""
        kws: list[str] = json.loads(existing_json or "[]")
        if keyword and keyword not in kws:
            kws.append(keyword)
        return json.dumps(kws, ensure_ascii=False)

    def _read_keywords(self, video_id: str) -> str:
        """读取已存储的 keywords JSON 字符串。"""
        row = self._conn.execute(
            "SELECT keywords FROM videos WHERE video_id=?", (video_id,)
        ).fetchone()
        return row["keywords"] if row else "[]"

    # ========== 写入 ==========

    def upsert_video(
        self,
        video_info: dict,
        *,
        is_mine: bool = False,
        keywords: list[str] | None = None,
    ) -> None:
        """从 get_video_info 返回的 dict 写入视频完整数据。已有数据则更新（含 raw_json）。"""
        now = _now_iso()
        raw = json.dumps(video_info, ensure_ascii=False)
        merged_kw = self._read_keywords(video_info.get("video_id", ""))
        for kw in keywords or []:
            merged_kw = self._merge_keyword(merged_kw, kw)

        video_id = video_info.get("video_id", "")
        video_url = video_info.get("url", "") or (f"https://www.douyin.com/video/{video_id}" if video_id else "")

        aweme_type = video_info.get("aweme_type", 0) or 0
        images_raw = video_info.get("images") or []
        images_json = json.dumps(images_raw, ensure_ascii=False) if images_raw else None

        self._conn.execute(
            """
            INSERT INTO videos (
                video_id, title, desc, author_id, author_name, author_sec_uid, author_signature,
                like_count, comment_count, collect_count, share_count,
                cover_url, video_url, play_url, duration, create_time,
                is_mine, aweme_type, images, keywords, raw_json, account, collected_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title           = excluded.title,
                desc            = excluded.desc,
                author_sec_uid  = excluded.author_sec_uid,
                author_signature = excluded.author_signature,
                like_count      = excluded.like_count,
                comment_count   = excluded.comment_count,
                collect_count   = excluded.collect_count,
                share_count     = excluded.share_count,
                cover_url       = excluded.cover_url,
                video_url       = excluded.video_url,
                play_url        = excluded.play_url,
                duration        = excluded.duration,
                create_time     = excluded.create_time,
                is_mine         = MAX(is_mine, excluded.is_mine),
                aweme_type      = excluded.aweme_type,
                images          = excluded.images,
                keywords        = excluded.keywords,
                raw_json        = excluded.raw_json,
                updated_at      = excluded.updated_at
            """,
            (
                video_id,
                video_info.get("title", ""),
                video_info.get("title", ""),
                video_info.get("author_id", ""),
                video_info.get("author", ""),
                video_info.get("author_sec_uid", ""),
                video_info.get("author_signature", ""),
                _parse_count(video_info.get("like_count", 0)),
                _parse_count(video_info.get("comment_count", 0)),
                _parse_count(video_info.get("collect_count", 0)),
                _parse_count(video_info.get("share_count", 0)),
                video_info.get("cover_url", ""),
                video_url,
                video_info.get("play_url", ""),
                video_info.get("duration") or None,
                video_info.get("create_time") or None,
                1 if is_mine else 0,
                aweme_type,
                images_json,
                merged_kw,
                raw,
                self._account,
                now,
                now,
            ),
        )
        self._conn.commit()

    def upsert_videos_from_feeds(
        self,
        feeds: list["Feed"],
        *,
        keyword: str | None = None,
    ) -> None:
        """从 Feed 列表写入轻量视频数据。

        - 首次写入时插入基础字段。
        - 再次写入时只更新互动计数和关键词，不影响 raw_json 等详情字段。
        """
        now = _now_iso()
        for feed in feeds:
            if not feed.video_id:
                continue
            merged_kw = self._merge_keyword(self._read_keywords(feed.video_id), keyword)
            url_path = "note" if feed.aweme_type == 68 else "video"
            video_url = feed.url or f"https://www.douyin.com/{url_path}/{feed.video_id}"
            images_json = json.dumps(feed.images, ensure_ascii=False) if feed.images else None

            self._conn.execute(
                """
                INSERT INTO videos (
                    video_id, title, desc, author_id, author_name, author_sec_uid, author_signature,
                    like_count, comment_count, collect_count, share_count,
                    cover_url, video_url, play_url, duration, is_mine,
                    aweme_type, images, keywords, account, collected_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title           = CASE WHEN raw_json IS NULL
                                        THEN excluded.title ELSE title END,
                    desc            = CASE WHEN raw_json IS NULL
                                        THEN excluded.desc ELSE desc END,
                    author_name     = CASE WHEN raw_json IS NULL
                                        THEN excluded.author_name ELSE author_name END,
                    author_sec_uid  = CASE WHEN raw_json IS NULL
                                        THEN excluded.author_sec_uid ELSE author_sec_uid END,
                    author_signature = CASE WHEN raw_json IS NULL
                                        THEN excluded.author_signature ELSE author_signature END,
                    like_count      = excluded.like_count,
                    comment_count   = excluded.comment_count,
                    collect_count   = excluded.collect_count,
                    share_count     = excluded.share_count,
                    cover_url       = COALESCE(cover_url, excluded.cover_url),
                    video_url       = excluded.video_url,
                    play_url        = COALESCE(play_url, excluded.play_url),
                    duration        = COALESCE(duration, excluded.duration),
                    aweme_type      = excluded.aweme_type,
                    images          = COALESCE(excluded.images, images),
                    keywords        = excluded.keywords,
                    updated_at      = excluded.updated_at
                """,
                (
                    feed.video_id,
                    feed.title,
                    feed.title,
                    feed.author_id,
                    feed.author,
                    feed.author_sec_uid or "",
                    feed.author_signature or "",
                    feed.like_count,
                    feed.comment_count,
                    feed.collect_count,
                    feed.share_count,
                    feed.cover_url or "",
                    video_url,
                    feed.play_url or "",
                    feed.duration or None,
                    0,  # is_mine
                    feed.aweme_type,
                    images_json,
                    merged_kw,
                    self._account,
                    now,
                    now,
                ),
            )
        self._conn.commit()

    def upsert_comments(
        self,
        comments: list[dict],
        video_id: str,
        *,
        my_author_id: str | None = None,
    ) -> None:
        """写入评论列表（fetch_comments 返回的 list[dict]）。

        is_mine 通过 my_author_id 或 me.json 中已存储的身份自动判断。
        抖音评论无 author_id，通过 author_name 匹配（若 me.json 存 nickname 则需扩展）。
        当前 me.json 存 author_id，评论 API 通常无 author_id，is_mine 依赖后续 mark_comments_mine 回溯。
        """
        known_my_id = my_author_id or self.get_my_author_id()
        now = _now_iso()
        rows = []
        for i, c in enumerate(comments):
            cid = str(c.get("comment_id", "") or c.get("cid", ""))
            if not cid:
                raw = f"{video_id}_{c.get('content','')}_{c.get('create_time',0)}_{i}"
                cid = "_" + hashlib.sha256(raw.encode()).hexdigest()[:16]
            author_name = c.get("author", "") or c.get("user", {}).get("nickname", "")
            author_id = c.get("author_id", "") or (c.get("user", {}) or {}).get("uid", "") or (c.get("user", {}) or {}).get("user_id", "")
            is_mine = 1 if (known_my_id and author_id == known_my_id) or (author_name and known_my_id and author_name == known_my_id) else 0
            rows.append((
                cid,
                video_id,
                None,
                c.get("content", "") or c.get("text", ""),
                author_id,
                author_name,
                is_mine,
                _parse_count(c.get("like_count", 0) or c.get("digg_count", 0)),
                c.get("create_time") or None,
                self._account,
                now,
            ))

        if not rows:
            return

        self._conn.executemany(
            """
            INSERT INTO comments (
                comment_id, video_id, parent_id, content,
                author_id, author_name, is_mine, like_count,
                create_time, account, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comment_id) DO UPDATE SET
                content      = excluded.content,
                like_count   = excluded.like_count,
                is_mine      = MAX(is_mine, excluded.is_mine),
                collected_at = excluded.collected_at
            """,
            rows,
        )
        self._conn.commit()

    def mark_videos_mine(self, video_ids: list[str]) -> None:
        """将指定 video_id 列表标记为 is_mine=1。供 my-profile 命令调用。"""
        if not video_ids:
            return
        placeholders = ",".join("?" * len(video_ids))
        self._conn.execute(
            f"UPDATE videos SET is_mine=1 WHERE video_id IN ({placeholders}) AND account=?",
            [*video_ids, self._account],
        )
        self._conn.commit()

    def mark_comments_mine(self, author_id: str) -> None:
        """将指定 author_id 的全部已存评论标记为 is_mine=1（回溯标记）。"""
        if not author_id:
            return
        self._conn.execute(
            "UPDATE comments SET is_mine=1 WHERE author_id=? AND account=?",
            (author_id, self._account),
        )
        self._conn.commit()

    # ========== 查询 ==========

    def query_videos(
        self,
        *,
        mine_only: bool = False,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict]:
        """查询视频列表。

        keyword 匹配 title、desc 或 keywords 字段（LIKE）。
        结果按 create_time 降序，未知时间的排在最后。
        """
        conditions = ["account = ?"]
        params: list = [self._account]
        if mine_only:
            conditions.append("is_mine = 1")
        if keyword:
            conditions.append("(title LIKE ? OR desc LIKE ? OR keywords LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"SELECT * FROM videos WHERE {where} "
            "ORDER BY COALESCE(create_time, 0) DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]

    def query_comments(
        self,
        *,
        video_id: str | None = None,
        mine_only: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """查询评论列表。

        可按 video_id 过滤（某视频的评论），可只看我发的（is_mine=1）。
        结果按 create_time 降序。
        """
        conditions = ["account = ?"]
        params: list = [self._account]
        if video_id:
            conditions.append("video_id = ?")
            params.append(video_id)
        if mine_only:
            conditions.append("is_mine = 1")
        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"SELECT * FROM comments WHERE {where} "
            "ORDER BY COALESCE(create_time, 0) DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def search_local(
        self,
        query: str,
        *,
        target: str = "videos",
        limit: int = 10,
    ) -> list[dict]:
        """在本地数据库全文 LIKE 检索。

        target: 'videos'（默认，匹配 title/desc/keywords）| 'comments'（匹配 content）
        """
        like = f"%{query}%"
        if target == "comments":
            rows = self._conn.execute(
                "SELECT * FROM comments WHERE account=? AND content LIKE ? "
                "ORDER BY COALESCE(create_time, 0) DESC LIMIT ?",
                (self._account, like, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM videos WHERE account=? AND (title LIKE ? OR desc LIKE ? OR keywords LIKE ?) "
                "ORDER BY COALESCE(create_time, 0) DESC LIMIT ?",
                (self._account, like, like, like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def trend_analysis(self, keyword: str, days: int = 30) -> dict:
        """按关键词统计近 N 天视频互动趋势（按采集日期分组）。"""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        like_kw = f"%{keyword}%"
        base_params = (self._account, like_kw, like_kw, like_kw, cutoff)

        rows = self._conn.execute(
            """
            SELECT
                DATE(collected_at)  AS date,
                COUNT(*)            AS video_count,
                AVG(like_count)     AS avg_likes,
                AVG(comment_count)  AS avg_comments,
                AVG(collect_count)  AS avg_collects
            FROM videos
            WHERE account = ?
              AND (title LIKE ? OR desc LIKE ? OR keywords LIKE ?)
              AND collected_at >= ?
            GROUP BY DATE(collected_at)
            ORDER BY date ASC
            """,
            base_params,
        ).fetchall()

        data_points = [
            {
                "date": r["date"],
                "video_count": r["video_count"],
                "avg_likes": round(r["avg_likes"] or 0, 1),
                "avg_comments": round(r["avg_comments"] or 0, 1),
                "avg_collects": round(r["avg_collects"] or 0, 1),
            }
            for r in rows
        ]

        summary = self._conn.execute(
            """
            SELECT COUNT(*) AS total, AVG(like_count) AS avg_likes,
                   AVG(comment_count) AS avg_comments
            FROM videos
            WHERE account = ?
              AND (title LIKE ? OR desc LIKE ? OR keywords LIKE ?)
              AND collected_at >= ?
            """,
            base_params,
        ).fetchone()

        return {
            "keyword": keyword,
            "days": days,
            "data_points": data_points,
            "summary": {
                "total_videos": summary["total"] if summary else 0,
                "avg_likes": round((summary["avg_likes"] or 0) if summary else 0, 1),
                "avg_comments": round((summary["avg_comments"] or 0) if summary else 0, 1),
            },
        }

    def get_video(self, video_id: str) -> dict | None:
        """按 video_id 查询单条视频，不存在则返回 None。"""
        row = self._conn.execute(
            "SELECT * FROM videos WHERE video_id=? AND account=?",
            (video_id, self._account),
        ).fetchone()
        return dict(row) if row else None

    # ========== 热榜话题 ==========

    @staticmethod
    def _generate_topic_id(word: str, date_str: str) -> str:
        """生成话题唯一 ID（word + date 的 SHA256 前 16 位）。"""
        return hashlib.sha256((word + date_str).encode("utf-8")).hexdigest()[:16]

    def save_hot_topics(self, topics: list) -> dict:
        """保存热榜话题到数据库（自动去重）。

        Args:
            topics: HotTopic 列表。

        Returns:
            {"found": int, "new": int, "errors": int}
        """
        if not topics:
            return {"found": 0, "new": 0, "errors": 0}

        now = datetime.now(UTC)
        today = now.strftime("%Y-%m-%d")
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        found = len(topics)
        new_count = 0
        errors = 0

        for topic in topics:
            try:
                topic_id = self._generate_topic_id(topic.word, today)

                self._conn.execute(
                    """
                    INSERT INTO hot_topics
                    (id, rank, word, hot_value, position, cover_url, word_type,
                     group_id, sentence_id, sentence_tag, event_time,
                     video_count, discuss_video_count, label, related_words,
                     search_url, fetched_at, fetch_date, account)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        hot_value           = excluded.hot_value,
                        position            = excluded.position,
                        cover_url           = excluded.cover_url,
                        video_count         = excluded.video_count,
                        discuss_video_count = excluded.discuss_video_count,
                        fetched_at          = excluded.fetched_at
                    """,
                    (
                        topic_id,
                        topic.rank,
                        topic.word,
                        topic.hot_value,
                        topic.position,
                        topic.cover_url,
                        topic.word_type,
                        topic.group_id,
                        topic.sentence_id,
                        topic.sentence_tag,
                        topic.event_time,
                        topic.video_count,
                        topic.discuss_video_count,
                        topic.label,
                        topic.related_words,
                        topic.search_url,
                        now_iso,
                        today,
                        self._account,
                    ),
                )
                new_count += 1

            except Exception as e:
                logger.warning("保存热榜话题失败 [%s]: %s", topic.word[:20], e)
                errors += 1
                continue

        self._conn.commit()
        logger.info("热榜保存完成: 发现 %d, 写入 %d, 错误 %d", found, new_count, errors)
        return {"found": found, "new": new_count, "errors": errors}

    def log_hot_fetch(
        self,
        found: int,
        new_count: int,
        video_count: int = 0,
        status: int = 0,
        error: str | None = None,
        started_at: str | None = None,
    ) -> None:
        """记录热榜抓取日志。"""
        now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        start = started_at or now_iso

        self._conn.execute(
            """
            INSERT INTO hot_fetch_logs
            (started_at, ended_at, found, new_count, video_count, status, error, account)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (start, now_iso, found, new_count, video_count, status, error, self._account),
        )
        self._conn.commit()

    def query_hot_topics(
        self,
        *,
        date_str: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """查询热榜话题。

        Args:
            date_str: 日期 YYYY-MM-DD，None 表示最新一次。
            limit: 返回数量上限。
        """
        if date_str:
            rows = self._conn.execute(
                """
                SELECT * FROM hot_topics
                WHERE fetch_date = ? AND account = ?
                ORDER BY rank ASC LIMIT ?
                """,
                (date_str, self._account, limit),
            ).fetchall()
        else:
            row = self._conn.execute(
                "SELECT fetch_date FROM hot_topics WHERE account = ? ORDER BY fetched_at DESC LIMIT 1",
                (self._account,),
            ).fetchone()
            if not row:
                return []
            latest_date = row["fetch_date"]
            rows = self._conn.execute(
                """
                SELECT * FROM hot_topics
                WHERE fetch_date = ? AND account = ?
                ORDER BY rank ASC LIMIT ?
                """,
                (latest_date, self._account, limit),
            ).fetchall()

        return [dict(r) for r in rows]

    def query_hot_stats(self, days: int = 7) -> list[dict]:
        """查询最近 N 天的热榜统计信息。"""
        rows = self._conn.execute(
            """
            SELECT fetch_date,
                   COUNT(*) as count,
                   COALESCE(SUM(hot_value), 0) as total_hot_value,
                   COALESCE(MAX(hot_value), 0) as max_hot_value,
                   MIN(fetched_at) as first_fetch,
                   MAX(fetched_at) as last_fetch
            FROM hot_topics
            WHERE account = ? AND fetch_date >= date('now', ? || ' days')
            GROUP BY fetch_date
            ORDER BY fetch_date DESC
            """,
            (self._account, f"-{days}"),
        ).fetchall()

        return [
            {
                "date": row["fetch_date"],
                "count": row["count"],
                "total_hot_value": row["total_hot_value"],
                "max_hot_value": row["max_hot_value"],
                "first_fetch": row["first_fetch"],
                "last_fetch": row["last_fetch"],
            }
            for row in rows
        ]

    def query_hot_logs(self, limit: int = 10) -> list[dict]:
        """查询热榜抓取日志。"""
        rows = self._conn.execute(
            """
            SELECT started_at, ended_at, found, new_count, video_count, status, error
            FROM hot_fetch_logs
            WHERE account = ?
            ORDER BY id DESC LIMIT ?
            """,
            (self._account, limit),
        ).fetchall()

        return [
            {
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "found": row["found"],
                "new_count": row["new_count"],
                "video_count": row["video_count"],
                "status": row["status"],
                "status_text": {0: "success", 1: "partial", 2: "failed"}.get(
                    row["status"], "unknown"
                ),
                "error": row["error"],
            }
            for row in rows
        ]
