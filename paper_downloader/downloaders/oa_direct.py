"""OA 直链下载器 — 最简单最快的策略"""

import logging
from pathlib import Path

import httpx

from .base import BaseDownloader
from ..models import PaperMetadata, DownloadResult
from ..constants import FailureReason
from ..validator import is_valid_pdf

logger = logging.getLogger(__name__)


class OADirectDownloader(BaseDownloader):
    """
    通过 OA PDF 直链下载。
    
    这是最优先的策略：
    - 不需要浏览器
    - 不需要解析页面
    - 速度最快
    """

    def __init__(self, download_dir: str | Path, timeout: int = 120):
        super().__init__(download_dir, timeout)
        self._client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/pdf,*/*",
            },
        )

    def close(self):
        self._client.close()

    def download(self, meta: PaperMetadata) -> DownloadResult:
        url = meta.oa_pdf_url
        if not url:
            return self._failed(meta, FailureReason.PDF_NOT_FOUND)

        target = self._target_path(meta)
        logger.info(f"OA 直链下载: {meta.doi} -> {url}")

        for attempt in range(1, 4):  # 最多 3 次
            try:
                with self._client.stream("GET", url) as resp:
                    if resp.status_code == 403:
                        logger.warning(f"403 拒绝访问: {url}")
                        return self._needs_manual(
                            meta, FailureReason.ACCESS_DENIED, url, attempt
                        )
                    if resp.status_code == 404:
                        logger.warning(f"404 未找到: {url}")
                        return self._failed(
                            meta, FailureReason.PDF_NOT_FOUND, url, attempt
                        )
                    if resp.status_code != 200:
                        logger.warning(f"HTTP {resp.status_code}: {url}")
                        continue

                    # 检查 Content-Type
                    ct = resp.headers.get("content-type", "")
                    if "html" in ct.lower():
                        logger.warning(f"返回 HTML 而非 PDF: {url}")
                        # 可能是需要登录或重定向到登录页
                        return self._needs_manual(
                            meta, FailureReason.ACCESS_DENIED, url, attempt
                        )

                    # 流式下载到文件
                    with open(target, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)

                # 校验 PDF
                if is_valid_pdf(target):
                    logger.info(f"下载成功: {meta.doi} -> {target}")
                    return self._success(meta, target, url, attempt)
                else:
                    logger.warning(f"PDF 校验失败: {target}")
                    target.unlink(missing_ok=True)
                    if attempt < 3:
                        continue
                    return self._failed(
                        meta, FailureReason.FILE_VALIDATION_FAILED, url, attempt
                    )

            except httpx.TimeoutException:
                logger.warning(f"下载超时 (尝试 {attempt}/3): {url}")
                target.unlink(missing_ok=True)
                if attempt < 3:
                    continue
                return self._failed(
                    meta, FailureReason.DOWNLOAD_TIMEOUT, url, attempt
                )
            except httpx.HTTPError as e:
                logger.error(f"HTTP 错误: {e}")
                target.unlink(missing_ok=True)
                return self._failed(
                    meta, FailureReason.UNKNOWN_ERROR, url, attempt
                )

        return self._failed(meta, FailureReason.UNKNOWN_ERROR, url, 3)
