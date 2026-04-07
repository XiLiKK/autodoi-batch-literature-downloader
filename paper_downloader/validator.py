"""PDF 文件校验"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# PDF 文件魔术字节
_PDF_MAGIC = b"%PDF"
# 最小合理 PDF 大小 (10KB)
_MIN_PDF_SIZE = 10 * 1024


def is_valid_pdf(file_path: str | Path) -> bool:
    """
    校验文件是否是合法 PDF。
    
    检查:
    1. 文件存在
    2. 文件大小 > 10KB
    3. 文件头是 %PDF
    4. 不是 HTML 伪装
    """
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"文件不存在: {path}")
        return False

    size = path.stat().st_size
    if size < _MIN_PDF_SIZE:
        logger.warning(f"文件太小 ({size} bytes): {path}")
        return False

    try:
        with open(path, "rb") as f:
            header = f.read(1024)

            # 检查 PDF 魔术字节
            if not header.startswith(_PDF_MAGIC):
                # 有些 PDF 前面有几个空字节
                pdf_pos = header.find(_PDF_MAGIC)
                if pdf_pos == -1 or pdf_pos > 100:
                    logger.warning(f"不是 PDF 文件 (魔术字节不匹配): {path}")
                    return False

            # 检查是否是 HTML 伪装成 PDF 扩展名
            header_str = header.decode("latin-1", errors="ignore").lower()
            if "<html" in header_str or "<!doctype" in header_str:
                logger.warning(f"HTML 伪装为 PDF: {path}")
                return False

    except Exception as e:
        logger.error(f"读取文件失败: {path} - {e}")
        return False

    return True
