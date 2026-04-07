"""核心数据结构定义"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .constants import TaskStatus, FailureReason, DownloadRoute


@dataclass
class DOITask:
    """CSV 读取后的原始任务"""
    row_id: int
    doi_raw: str
    doi_normalized: str
    status: TaskStatus = TaskStatus.PENDING


@dataclass
class PaperMetadata:
    """论文元数据"""
    doi: str
    title: str = "UNTITLED"
    year: str = "UNKYEAR"
    journal: str = ""
    journal_abbr: str = "UNKVENUE"
    first_author: str = "UNKAUTHOR"
    publisher: str = ""
    landing_url: str = ""
    oa_pdf_url: Optional[str] = None
    target_filename: str = ""


@dataclass
class DownloadResult:
    """下载执行结果"""
    doi: str
    status: TaskStatus
    local_path: str = ""
    source_url: str = ""
    failure_reason: Optional[FailureReason] = None
    attempt_count: int = 1
    processed_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ManualQueueItem:
    """人工确认队列条目"""
    doi: str
    title: str = ""
    publisher: str = ""
    landing_url: str = ""
    blocked_reason: str = ""
    suggested_action: str = "在浏览器中手动下载 PDF"
    opened_by_user: bool = False
    resolved: bool = False
