"""抖音热榜数据库操作模块。

使用 SQLite 持久化存储热榜数据，支持历史查询和趋势分析。
数据库位置: ~/.dingclaw/store-douyin/hot_list.db
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR
from .types import HotItem

logger = logging.getLogger(__name__)

# 数据库文件路径
DB_PATH = DATA_DIR / "hot_list.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接（自动初始化）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    need_init = not DB_PATH.exists()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if need_init:
        _create_tables(conn)
    return conn


def _create_tables(conn: sqlite3.Connection) -> None:
    """创建数据库表和索引。"""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hot_items (
            id TEXT PRIMARY KEY,
            rank INTEGER,
            title TEXT NOT NULL,
            hot_value INTEGER DEFAULT 0,
            link TEXT,
            cover TEXT,
            label TEXT,
            item_type TEXT,
            fetched_at TEXT,
            fetch_date TEXT
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_hot_items_date ON hot_items(fetch_date)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_hot_items_rank ON hot_items(rank)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_hot_items_fetched ON hot_items(fetched_at)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fetch_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            ended_at TEXT,
            found INTEGER DEFAULT 0,
            new_count INTEGER DEFAULT 0,
            status INTEGER DEFAULT 0,
            error TEXT
        )
    """)

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_fetch_logs_started ON fetch_logs(started_at)"
    )

    conn.commit()
    logger.info("热榜数据库已初始化: %s", DB_PATH)


def init_db() -> Path:
    """初始化数据库（幂等操作）。

    Returns:
        数据库文件路径。
    """
    conn = _get_conn()
    _create_tables(conn)
    conn.close()
    return DB_PATH


def _generate_id(title: str, date_str: str) -> str:
    """生成条目唯一 ID（title + date 的 SHA256 前 16 位）。"""
    return hashlib.sha256((title + date_str).encode("utf-8")).hexdigest()[:16]


def save_hot_items(items: list[HotItem]) -> dict:
    """保存热榜条目到数据库（自动去重）。

    Args:
        items: HotItem 列表。

    Returns:
        {"found": int, "new": int, "errors": int}
    """
    if not items:
        return {"found": 0, "new": 0, "errors": 0}

    conn = _get_conn()
    cursor = conn.cursor()

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    now_iso = now.isoformat()

    found = len(items)
    new_count = 0
    errors = 0

    for item in items:
        try:
            item_id = _generate_id(item.title, today)

            # 检查是否已存在
            cursor.execute("SELECT id FROM hot_items WHERE id = ?", (item_id,))
            if cursor.fetchone():
                continue

            cursor.execute(
                """
                INSERT INTO hot_items
                (id, rank, title, hot_value, link, cover, label, item_type, fetched_at, fetch_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    item.rank,
                    item.title,
                    item.hot_value,
                    item.link,
                    item.cover,
                    item.label,
                    item.item_type,
                    now_iso,
                    today,
                ),
            )
            new_count += 1

        except Exception as e:
            logger.warning("保存热榜条目失败 [%s]: %s", item.title[:20], e)
            errors += 1
            continue

    conn.commit()
    conn.close()

    logger.info("热榜保存完成: 发现 %d, 新增 %d, 错误 %d", found, new_count, errors)
    return {"found": found, "new": new_count, "errors": errors}


def log_fetch(
    found: int,
    new_count: int,
    status: int = 0,
    error: str | None = None,
    started_at: str | None = None,
) -> None:
    """记录抓取日志。

    Args:
        found: 发现条目数。
        new_count: 新增条目数。
        status: 状态码（0=成功, 1=部分失败, 2=失败）。
        error: 错误信息。
        started_at: 开始时间 ISO 格式（None 则使用当前时间）。
    """
    conn = _get_conn()
    cursor = conn.cursor()

    now_iso = datetime.now(timezone.utc).isoformat()
    start = started_at or now_iso

    cursor.execute(
        """
        INSERT INTO fetch_logs (started_at, ended_at, found, new_count, status, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (start, now_iso, found, new_count, status, error),
    )

    conn.commit()
    conn.close()


def query_by_date(date_str: str | None = None, limit: int = 50) -> list[dict]:
    """查询指定日期的热榜条目。

    Args:
        date_str: 日期字符串 YYYY-MM-DD，None 表示今天。
        limit: 返回数量上限。

    Returns:
        条目字典列表。
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT rank, title, hot_value, link, cover, label, item_type, fetched_at, fetch_date
        FROM hot_items
        WHERE fetch_date = ?
        ORDER BY rank ASC
        LIMIT ?
        """,
        (date_str, limit),
    )

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "rank": row["rank"],
            "title": row["title"],
            "hot_value": row["hot_value"],
            "link": row["link"],
            "cover": row["cover"],
            "label": row["label"],
            "item_type": row["item_type"],
            "fetched_at": row["fetched_at"],
            "fetch_date": row["fetch_date"],
        }
        for row in rows
    ]


def query_latest(limit: int = 50) -> list[dict]:
    """查询最新一次抓取的热榜数据。

    Args:
        limit: 返回数量上限。

    Returns:
        条目字典列表。
    """
    conn = _get_conn()
    cursor = conn.cursor()

    # 先获取最新的 fetch_date
    cursor.execute(
        "SELECT fetch_date FROM hot_items ORDER BY fetched_at DESC LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return []

    latest_date = row["fetch_date"]

    cursor.execute(
        """
        SELECT rank, title, hot_value, link, cover, label, item_type, fetched_at, fetch_date
        FROM hot_items
        WHERE fetch_date = ?
        ORDER BY rank ASC
        LIMIT ?
        """,
        (latest_date, limit),
    )

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "rank": row["rank"],
            "title": row["title"],
            "hot_value": row["hot_value"],
            "link": row["link"],
            "cover": row["cover"],
            "label": row["label"],
            "item_type": row["item_type"],
            "fetched_at": row["fetched_at"],
            "fetch_date": row["fetch_date"],
        }
        for row in rows
    ]


def query_stats(days: int = 7) -> list[dict]:
    """查询最近 N 天的统计信息。

    Args:
        days: 统计天数。

    Returns:
        每天的统计字典列表。
    """
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT fetch_date,
               COUNT(*) as count,
               COALESCE(SUM(hot_value), 0) as total_hot_value,
               COALESCE(MAX(hot_value), 0) as max_hot_value,
               MIN(fetched_at) as first_fetch,
               MAX(fetched_at) as last_fetch
        FROM hot_items
        WHERE fetch_date >= date('now', ? || ' days')
        GROUP BY fetch_date
        ORDER BY fetch_date DESC
        """,
        (f"-{days}",),
    )

    rows = cursor.fetchall()
    conn.close()

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


def query_logs(limit: int = 10) -> list[dict]:
    """查询抓取日志。

    Args:
        limit: 返回数量上限。

    Returns:
        日志字典列表。
    """
    conn = _get_conn()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT started_at, ended_at, found, new_count, status, error
        FROM fetch_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "found": row["found"],
            "new_count": row["new_count"],
            "status": row["status"],
            "status_text": {0: "success", 1: "partial", 2: "failed"}.get(
                row["status"], "unknown"
            ),
            "error": row["error"],
        }
        for row in rows
    ]
