"""模块1：CSV 输入与 DOI 预处理"""

import csv
import re
from pathlib import Path

from .models import DOITask


# DOI 正则：10.xxxx/xxxxx
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")

# 常见 DOI 前缀需要去掉
_DOI_PREFIXES = [
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
    "DOI:",
]


def normalize_doi(raw: str) -> str:
    """
    规范化 DOI：
    - 去首尾空格
    - 去掉常见 URL 前缀
    - 不转小写（DOI 大小写敏感的部分在后缀，但前缀 10.xxxx 不敏感）
    """
    s = raw.strip()
    for prefix in _DOI_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # 去掉尾部可能的句号、逗号
    s = s.rstrip(".,;")
    return s


def is_valid_doi(doi: str) -> bool:
    """检查 DOI 格式是否合法"""
    return bool(_DOI_PATTERN.match(doi))


def read_doi_csv(csv_path: str | Path) -> list[DOITask]:
    """
    读取 CSV 文件，提取 DOI 列，返回去重后的任务列表。
    
    - 兼容列名 DOI / doi
    - 自动跳过空行
    - 自动去重（保留首次出现）
    - 标记无效 DOI
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 文件不存在: {csv_path}")

    tasks: list[DOITask] = []
    seen_dois: set[str] = set()
    row_id = 0

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        # 自动探测分隔符
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel  # fallback to comma

        reader = csv.DictReader(f, dialect=dialect)

        # 查找 DOI 列名
        if reader.fieldnames is None:
            raise ValueError("CSV 文件为空或无表头")

        doi_col = None
        for col in reader.fieldnames:
            if col.strip().upper() == "DOI":
                doi_col = col
                break

        if doi_col is None:
            raise ValueError(
                f"CSV 中未找到 DOI 列。现有列名: {reader.fieldnames}"
            )

        for row in reader:
            raw = row.get(doi_col, "").strip()
            if not raw:
                continue  # 跳过空行

            normalized = normalize_doi(raw)
            
            if normalized in seen_dois:
                continue  # 去重

            seen_dois.add(normalized)
            row_id += 1

            tasks.append(DOITask(
                row_id=row_id,
                doi_raw=raw,
                doi_normalized=normalized,
            ))

    return tasks
