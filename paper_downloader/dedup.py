"""模块3：重复检测"""

import logging
from pathlib import Path

from .db import Database
from .constants import TaskStatus

logger = logging.getLogger(__name__)


def check_duplicate(doi: str, db: Database, download_dir: str | Path) -> bool:
    """
    检查 DOI 是否已存在（已下载或已跳过）。
    
    检查:
    1. 数据库中是否有成功下载记录
    2. 已回录路径的文件是否仍然存在
    
    Returns:
        True 如果已存在（应跳过），False 如果需要下载
    """
    # 检查数据库
    if db.has_successful_download(doi):
        existing_path = db.get_download_path(doi)
        if existing_path and Path(existing_path).exists():
            logger.info(f"本地已存在 (文件确认): {doi} -> {existing_path}")
            return True
        elif existing_path:
            # 数据库有记录但文件不在了，需要重新下载
            logger.warning(f"数据库有记录但文件缺失，将重新下载: {doi}")
            return False
        else:
            # SKIPPED_ALREADY_HAVE 记录没有路径，也算已存在
            logger.info(f"本地已存在 (数据库记录): {doi}")
            return True

    return False
