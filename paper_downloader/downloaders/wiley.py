"""Wiley Online Library 下载策略器"""

import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from .base import BrowserDownloader
from ..models import PaperMetadata, DownloadResult
from ..constants import FailureReason
from ..validator import is_valid_pdf

logger = logging.getLogger(__name__)


class WileyDownloader(BrowserDownloader):
    """
    Wiley 下载策略。
    """

    PDF_SELECTORS = [
        'a.epub-section__item[href*="/doi/pdf/"]',
        'a[href*="/doi/pdf/"]',
        'a.pdf-download',
        'a[title*="PDF"]',
        'a[data-track-action*="pdf"]',
        'a.article-tool__item--pdf',
        '.coolBar--download a',
        'a[href*="epdf"]',
    ]

    COOKIE_SELECTORS = [
        '#onetrust-accept-btn-handler',
        'button[data-cc-action="accept"]',
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        '.osano-cm-accept-all',
    ]

    def download(self, meta: PaperMetadata) -> DownloadResult:
        result = self._try_direct_pdf(meta)
        if result and result.status.value == "SUCCESS_AUTO":
            return result
        return self._try_browser(meta)

    def _try_direct_pdf(self, meta: PaperMetadata) -> Optional[DownloadResult]:
        target = self._target_path(meta)
        doi = meta.doi

        urls_to_try = [
            f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true",
            f"https://onlinelibrary.wiley.com/doi/pdf/{doi}",
        ]

        client = httpx.Client(
            timeout=self.timeout,
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

        try:
            for url in urls_to_try:
                try:
                    logger.info(f"Wiley 直链尝试: {url}")
                    resp = client.get(url)
                    ct = resp.headers.get("content-type", "")

                    if resp.status_code == 200 and "pdf" in ct.lower():
                        with open(target, "wb") as f:
                            f.write(resp.content)
                        if is_valid_pdf(target):
                            logger.info(f"Wiley 直链成功: {meta.doi}")
                            return self._success(meta, target, url)
                        target.unlink(missing_ok=True)
                except httpx.HTTPError as e:
                    logger.debug(f"直链失败: {url} - {e}")
        finally:
            client.close()

        return None

    def _try_browser(self, meta: PaperMetadata) -> DownloadResult:
        target = self._target_path(meta)
        url = meta.landing_url

        try:
            tab = self.create_stealth_tab()

            logger.info(f"Wiley 浏览器访问: {url}")
            try:
                tab.get(url)
            except Exception as e:
                logger.debug(f"tab.get 异常(可恢复): {e}")
            time.sleep(2)

            self._dismiss_cookie_banner(tab)

            for selector in self.PDF_SELECTORS:
                try:
                    s = f"css:{selector}" if not selector.startswith(('xpath:', '@', 'text=', 'text:')) else selector
                    el = tab.ele(s, timeout=2)
                    if el:
                        parent_a = el if el.tag == 'a' else el.parent('tag:a')
                        href = parent_a.attr("href") if parent_a else el.attr("href")
                        
                        logger.info(f"Wiley 找到 PDF: {selector} -> {href}")

                        if href:
                            if "epdf" in href:
                                pdf_href = href.replace("/epdf/", "/pdfdirect/")
                                if "?" in pdf_href:
                                    pdf_href += "&download=true"
                                else:
                                    pdf_href += "?download=true"
                            else:
                                pdf_href = href
                                
                            if pdf_href.startswith('/'):
                                from urllib.parse import urlparse
                                parsed = urlparse(tab.url)
                                pdf_href = f"{parsed.scheme}://{parsed.netloc}{pdf_href}"

                            cookies_dict = {c['name']: c['value'] for c in tab.cookies()} if tab.cookies() else None
                            result = self._download_from_url(pdf_href, target, meta, cookies=cookies_dict)
                            if result and result.status.value == "SUCCESS_AUTO":
                                tab.close()
                                return result

                        try:
                            tab.set.download_path(str(target.parent))
                            tab.set.download_file_name(target.name)
                            if parent_a:
                                parent_a.click()
                            else:
                                el.click()
                            
                            tab.wait.download_begin(timeout=10)
                            
                            wait_count = 0
                            while wait_count < self.timeout:
                                if target.exists() and is_valid_pdf(target):
                                    break
                                time.sleep(1)
                                wait_count += 1

                            if target.exists() and is_valid_pdf(target):
                                logger.info(f"Wiley 下载成功: {meta.doi}")
                                tab.close()
                                return self._success(meta, target, url)
                            target.unlink(missing_ok=True)
                        except Exception as e:
                            logger.debug(f"下载事件失败: {e}")
                except Exception as e:
                    logger.debug(f"选择器 {selector} 失败: {e}")
                    continue

            tab.close()

            logger.warning(f"Wiley 未找到 PDF 下载入口: {meta.doi}")
            return self._needs_manual(meta, FailureReason.PDF_NOT_FOUND, url)

        except Exception as e:
            logger.error(f"Wiley 浏览器异常: {e}")
            target.unlink(missing_ok=True)
            return self._needs_manual(meta, FailureReason.UNKNOWN_ERROR, url)

    def _download_from_url(self, url: str, target: Path,
                           meta: PaperMetadata, cookies: dict = None) -> DownloadResult:
        try:
            client = httpx.Client(timeout=self.timeout, follow_redirects=True, cookies=cookies)
            resp = client.get(url)
            client.close()
            if resp.status_code == 200:
                with open(target, "wb") as f:
                    f.write(resp.content)
                if is_valid_pdf(target):
                    return self._success(meta, target, url)
                target.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"URL 下载失败: {e}")
        return self._needs_manual(meta, FailureReason.PDF_NOT_FOUND, url)
