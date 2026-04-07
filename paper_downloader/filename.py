"""文件命名生成模块"""

import re
import unicodedata


# Windows 文件名非法字符
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
# 连续空格/下划线
_MULTI_SPACE = re.compile(r"[\s_]+")


def sanitize_filename(s: str) -> str:
    """
    清理文件名中的非法字符：
    - 替换 Windows 非法字符为空格
    - NFKD 规范化 unicode
    - 去除控制字符
    - 合并连续空格
    - 去首尾空格
    """
    # 替换非法字符
    s = _ILLEGAL_CHARS.sub(" ", s)
    # 规范化 unicode
    s = unicodedata.normalize("NFKD", s)
    # 去控制字符
    s = "".join(c for c in s if not unicodedata.category(c).startswith("C"))
    # 合并空格
    s = _MULTI_SPACE.sub(" ", s)
    return s.strip()


def generate_journal_abbr(journal_name: str) -> str:
    """
    从期刊全名生成首字母缩写。
    例如: "Nature Materials" -> "NM"
         "Journal of the American Chemical Society" -> "JACS"
    
    跳过常见虚词。
    """
    if not journal_name:
        return "UNKVENUE"

    # 虚词列表（缩写时跳过）
    skip_words = {
        "of", "the", "and", "for", "in", "on", "at", "to", "a", "an",
        "de", "des", "du", "la", "le", "les", "et", "und", "der", "die"
    }

    words = journal_name.split()
    initials = []
    for w in words:
        w_clean = w.strip(".,;:()")
        if w_clean.lower() in skip_words:
            continue
        if w_clean:
            initials.append(w_clean[0].upper())

    abbr = "".join(initials)
    return abbr if abbr else "UNKVENUE"


def generate_filename(
    doi: str,
    title: str = "UNTITLED",
    year: str = "UNKYEAR",
    journal_abbr: str = "UNKVENUE",
    first_author: str = "UNKAUTHOR",
    max_title_length: int = 80,
) -> str:
    """
    生成论文 PDF 文件名。
    
    格式: YYYY_ABBR_Author_Title.pdf
    例如: 2024_NM_Wang_Deep learning for protein design.pdf
    """
    # 清理各字段
    year = sanitize_filename(year) if year else "UNKYEAR"
    journal_abbr = sanitize_filename(journal_abbr) if journal_abbr else "UNKVENUE"
    first_author = sanitize_filename(first_author) if first_author else "UNKAUTHOR"
    title = sanitize_filename(title) if title else "UNTITLED"

    # 截断题目
    if len(title) > max_title_length:
        title = title[:max_title_length].rstrip() + "..."

    filename = f"{year}_{journal_abbr}_{first_author}_{title}.pdf"

    # 最终安全检查：Windows 文件名最长 255 字符
    if len(filename) > 250:
        filename = filename[:246] + "....pdf"

    return filename
