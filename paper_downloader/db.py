"""模块3：SQLite 数据库管理"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from .models import PaperMetadata, DownloadResult, ManualQueueItem
from .constants import TaskStatus

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库管理器"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        """建表"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS papers (
                doi TEXT PRIMARY KEY,
                title TEXT,
                year TEXT,
                journal TEXT,
                journal_abbr TEXT,
                first_author TEXT,
                publisher TEXT,
                landing_url TEXT,
                target_filename TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT NOT NULL,
                status TEXT NOT NULL,
                local_path TEXT,
                source_url TEXT,
                failure_reason TEXT,
                attempt_count INTEGER DEFAULT 1,
                processed_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (doi) REFERENCES papers(doi)
            );

            CREATE INDEX IF NOT EXISTS idx_downloads_doi ON downloads(doi);
            CREATE INDEX IF NOT EXISTS idx_downloads_status ON downloads(status);

            CREATE TABLE IF NOT EXISTS manual_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT NOT NULL,
                landing_url TEXT,
                blocked_reason TEXT,
                suggested_action TEXT,
                resolved INTEGER DEFAULT 0,
                opened_at TEXT,
                FOREIGN KEY (doi) REFERENCES papers(doi)
            );

            CREATE INDEX IF NOT EXISTS idx_manual_queue_resolved ON manual_queue(resolved);
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ---- papers 表操作 ----

    def upsert_paper(self, meta: PaperMetadata):
        """插入或更新论文元数据"""
        self.conn.execute("""
            INSERT INTO papers (doi, title, year, journal, journal_abbr,
                              first_author, publisher, landing_url, target_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(doi) DO UPDATE SET
                title=excluded.title,
                year=excluded.year,
                journal=excluded.journal,
                journal_abbr=excluded.journal_abbr,
                first_author=excluded.first_author,
                publisher=excluded.publisher,
                landing_url=excluded.landing_url,
                target_filename=excluded.target_filename
        """, (
            meta.doi, meta.title, meta.year, meta.journal,
            meta.journal_abbr, meta.first_author, meta.publisher,
            meta.landing_url, meta.target_filename,
        ))
        self.conn.commit()

    def get_paper(self, doi: str) -> Optional[dict]:
        """获取论文记录"""
        row = self.conn.execute(
            "SELECT * FROM papers WHERE doi = ?", (doi,)
        ).fetchone()
        return dict(row) if row else None

    # ---- downloads 表操作 ----

    def has_successful_download(self, doi: str) -> bool:
        """检查 DOI 是否已有成功下载"""
        row = self.conn.execute("""
            SELECT 1 FROM downloads
            WHERE doi = ? AND status IN (?, ?)
            LIMIT 1
        """, (doi, TaskStatus.SUCCESS_AUTO, TaskStatus.SKIPPED_ALREADY_HAVE)).fetchone()
        return row is not None

    def get_download_path(self, doi: str) -> Optional[str]:
        """获取已下载文件路径"""
        row = self.conn.execute("""
            SELECT local_path FROM downloads
            WHERE doi = ? AND status = ? AND local_path IS NOT NULL
            ORDER BY processed_at DESC LIMIT 1
        """, (doi, TaskStatus.SUCCESS_AUTO)).fetchone()
        return row["local_path"] if row else None

    def record_download(self, result: DownloadResult):
        """记录下载结果"""
        self.conn.execute("""
            INSERT INTO downloads (doi, status, local_path, source_url,
                                  failure_reason, attempt_count, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            result.doi, result.status, result.local_path,
            result.source_url, result.failure_reason,
            result.attempt_count, result.processed_at,
        ))
        self.conn.commit()

    def get_all_downloads(self) -> list[dict]:
        """获取所有下载记录"""
        rows = self.conn.execute("""
            SELECT d.*, p.title, p.year, p.journal_abbr, p.first_author
            FROM downloads d
            LEFT JOIN papers p ON d.doi = p.doi
            ORDER BY d.processed_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_downloads_by_status(self, status: TaskStatus) -> list[dict]:
        """按状态查询下载记录"""
        rows = self.conn.execute("""
            SELECT d.*, p.title, p.year, p.journal_abbr, p.first_author
            FROM downloads d
            LEFT JOIN papers p ON d.doi = p.doi
            WHERE d.status = ?
            ORDER BY d.processed_at DESC
        """, (status,)).fetchall()
        return [dict(r) for r in rows]

    # ---- manual_queue 表操作 ----

    def add_to_manual_queue(self, item: ManualQueueItem):
        """添加到人工队列"""
        self.conn.execute("""
            INSERT INTO manual_queue (doi, landing_url, blocked_reason,
                                     suggested_action, resolved)
            VALUES (?, ?, ?, ?, 0)
        """, (
            item.doi, item.landing_url,
            item.blocked_reason, item.suggested_action,
        ))
        self.conn.commit()

    def get_pending_manual_items(self) -> list[dict]:
        """获取未解决的人工队列项"""
        rows = self.conn.execute("""
            SELECT mq.*, p.title, p.publisher
            FROM manual_queue mq
            LEFT JOIN papers p ON mq.doi = p.doi
            WHERE mq.resolved = 0
            ORDER BY mq.id
        """).fetchall()
        return [dict(r) for r in rows]

    def resolve_manual_item(self, item_id: int, resolved: bool = True):
        """标记人工队列项为已解决"""
        self.conn.execute("""
            UPDATE manual_queue
            SET resolved = ?, opened_at = ?
            WHERE id = ?
        """, (int(resolved), datetime.now().isoformat(), item_id))
        self.conn.commit()

    # ---- 统计 ----

    def get_stats(self) -> dict:
        """获取下载统计"""
        stats = {}
        for status in TaskStatus:
            row = self.conn.execute(
                "SELECT COUNT(*) as cnt FROM downloads WHERE status = ?",
                (status,)
            ).fetchone()
            stats[status.value] = row["cnt"]

        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM manual_queue WHERE resolved = 0"
        ).fetchone()
        stats["manual_pending"] = row["cnt"]

        return stats
