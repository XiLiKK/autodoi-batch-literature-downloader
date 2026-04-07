"""通用下载策略器"""

import logging
import time
from pathlib import Path

import httpx

from .base import BrowserDownloader
from ..models import PaperMetadata, DownloadResult
from ..constants import FailureReason
from ..validator import is_valid_pdf

logger = logging.getLogger(__name__)


class GenericDownloader(BrowserDownloader):
    """
    通用下载策略：适用于不在已知出版商列表中的网站。
    """

    GENERIC_PDF_SELECTORS = [
        'a[href$=".pdf"]',
        'a[href*="/pdf/"]',
        'a[href*="pdf"]',
        'a[title*="PDF"]',
        'a[title*="pdf"]',
        'a:has-text("Download PDF")',
        'a:has-text("PDF")',
        'a.pdf-download',
        'a[data-track-action*="pdf"]',
        'button:has-text("PDF")',
    ]

    COOKIE_SELECTORS = [
        '#onetrust-accept-btn-handler',
        'button[data-cc-action="accept"]',
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
        'button:has-text("Got it")',
        'button:has-text("Close")',
        'button[aria-label="Close"]',
        '.modal-close',
    ]

    def download(self, meta: PaperMetadata) -> DownloadResult:
        # 策略 1：直接 HTTP 请求 landing URL
        result = self._try_http_direct(meta)
        if result and result.status.value == "SUCCESS_AUTO":
            return result

        # 策略 2：浏览器自动化
        return self._try_browser(meta)

    def _try_http_direct(self, meta: PaperMetadata) -> DownloadResult | None:
        target = self._target_path(meta)
        url = meta.landing_url

        if not url:
            return None

        try:
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
            resp = client.get(url)
            client.close()
            ct = resp.headers.get("content-type", "")

            if resp.status_code == 200 and "pdf" in ct.lower():
                with open(target, "wb") as f:
                    f.write(resp.content)
                if is_valid_pdf(target):
                    logger.info(f"通用直链成功: {meta.doi}")
                    return self._success(meta, target, url)
                target.unlink(missing_ok=True)
        except Exception as e:
            logger.debug(f"通用直链失败: {e}")

        return None

    def _try_browser(self, meta: PaperMetadata) -> DownloadResult:
        target = self._target_path(meta)
        url = meta.landing_url

        try:
            tab = self.create_stealth_tab()
            # tab.set.timeouts(page_load=self.page_timeout) # Already done in base

            logger.info(f"通用浏览器访问: {url}")
            try:
                tab.get(url)
            except Exception as e:
                logger.debug(f"tab.get 异常(可恢复): {e}")
            time.sleep(2)

            self._dismiss_cookie_banner(tab)

            for selector in self.GENERIC_PDF_SELECTORS:
                try:
                    s = f"css:{selector}" if not selector.startswith(('xpath:', '@', 'text=', 'text:')) else selector
                    elements = tab.eles(s, timeout=1)
                    for el in elements:
                        parent_a = el if el.tag == 'a' else el.parent('tag:a')
                        href = parent_a.attr("href") if parent_a else el.attr("href")
                        href = href or ""

                        # 跳过明显不相关的链接
                        if any(skip in href.lower() for skip in [
                            "supplementary", "supporting", "appendix", "table"
                        ]):
                            continue

                        logger.info(f"通用策略找到候选: {href[:80]}")

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
                                logger.info(f"通用下载成功: {meta.doi}")
                                tab.close()
                                return self._success(meta, target, url)
                            target.unlink(missing_ok=True)
                        except Exception:
                            new_url = tab.url
                            if "pdf" in new_url.lower():
                                cookies_dict = {c['name']: c['value'] for c in tab.cookies()} if tab.cookies() else None
                                result = self._download_url(new_url, target, meta, cookies=cookies_dict)
                                if result:
                                    tab.close()
                                    return result
                except Exception as e:
                    logger.debug(f"选择器 {selector} 失败: {e}")

            tab.close()

            logger.warning(f"通用策略未找到 PDF: {meta.doi}")
            return self._needs_manual(meta, FailureReason.PDF_NOT_FOUND, url)

        except Exception as e:
            logger.error(f"通用浏览器异常: {e}")
            target.unlink(missing_ok=True)
            return self._needs_manual(meta, FailureReason.UNKNOWN_ERROR, url)

    def _download_url(self, url: str, target: Path,
                      meta: PaperMetadata, cookies: dict = None) -> DownloadResult | None:
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
        except Exception:
            pass
        return None
