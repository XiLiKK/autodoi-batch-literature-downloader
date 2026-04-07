"""Springer/Nature 下载策略器"""

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


class SpringerDownloader(BrowserDownloader):
    """
    Springer/Nature/BMC 下载策略。
    
    常见模式:
    - link.springer.com/content/pdf/DOI.pdf (直链)
    - nature.com/articles/ID.pdf (直链)
    - 页面中的 PDF 按钮
    """

    PDF_SELECTORS = [
        'a[data-article-pdf]',
        'a[data-track-action="download pdf"]',
        'a.c-pdf-download__link',
        'a[href*=".pdf"]',
        'a.pdf-download',
        'a[title*="Download PDF"]',
        'a[aria-label*="Download PDF"]',
        '#cobranding-and-download-availability-text a',
        '.c-pdf-download a',
        'a.u-button--primary[href*="pdf"]',
    ]

    COOKIE_SELECTORS = [
        'button[data-cc-action="accept"]',
        'button.cc-accept',
        '#onetrust-accept-btn-handler',
        'button[aria-label="Accept cookies"]',
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
    ]

    def download(self, meta: PaperMetadata) -> DownloadResult:
        # 策略 1：先尝试构造直链
        result = self._try_direct_pdf(meta)
        if result and result.status.value == "SUCCESS_AUTO":
            return result

        # 策略 2：用浏览器自动化
        return self._try_browser(meta)

    def _try_direct_pdf(self, meta: PaperMetadata) -> Optional[DownloadResult]:
        """尝试 Springer 直链模式"""
        doi = meta.doi
        target = self._target_path(meta)

        urls_to_try = [
            f"https://link.springer.com/content/pdf/{doi}.pdf",
        ]

        # 如果 landing_url 包含 nature.com，尝试 Nature 直链
        if "nature.com" in meta.landing_url:
            parts = meta.landing_url.rstrip("/").split("/")
            if parts:
                article_id = parts[-1]
                urls_to_try.insert(0, f"https://www.nature.com/articles/{article_id}.pdf")

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
                    logger.info(f"Springer 直链尝试: {url}")
                    resp = client.get(url)
                    ct = resp.headers.get("content-type", "")

                    if resp.status_code == 200 and ("pdf" in ct.lower() or "octet" in ct.lower()):
                        with open(target, "wb") as f:
                            f.write(resp.content)

                        if is_valid_pdf(target):
                            logger.info(f"Springer 直链成功: {meta.doi}")
                            return self._success(meta, target, url)
                        target.unlink(missing_ok=True)

                except httpx.HTTPError as e:
                    logger.debug(f"直链失败: {url} - {e}")
                    continue
        finally:
            client.close()

        return None

    def _try_browser(self, meta: PaperMetadata) -> DownloadResult:
        """用 DrissionPage 浏览器自动化下载"""
        target = self._target_path(meta)
        url = meta.landing_url

        try:
            tab = self.create_stealth_tab()

            logger.info(f"Springer 浏览器访问: {url}")
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
                        
                        logger.info(f"Springer 找到 PDF 按钮: {selector}")
                        
                        if href and ('pdf' in href.lower() or 'download' in href.lower()):
                            logger.info(f"提取到原生直链: {href}")
                            # 拼全 URL
                            if href.startswith('/'):
                                from urllib.parse import urlparse
                                parsed = urlparse(tab.url)
                                full_href = f"{parsed.scheme}://{parsed.netloc}{href}"
                            else:
                                full_href = href
                                
                            cookies_dict = {c['name']: c['value'] for c in tab.cookies()} if tab.cookies() else None
                            result = self._download_from_url(full_href, target, meta, cookies=cookies_dict)
                            if result and result.status.value == "SUCCESS_AUTO":
                                tab.close()
                                return result

                        tab.set.download_path(str(target.parent))
                        tab.set.download_file_name(target.name)
                        try:
                            if parent_a:
                                parent_a.click()
                            else:
                                el.click()
                        except Exception:
                            if parent_a:
                                parent_a.click(by_js=True)
                            else:
                                el.click(by_js=True)
                        
                        tab.wait.download_begin(timeout=10)
                        
                        wait_count = 0
                        while wait_count < self.timeout:
                            if target.exists() and is_valid_pdf(target):
                                break
                            time.sleep(1)
                            wait_count += 1

                        if target.exists() and is_valid_pdf(target):
                            logger.info(f"Springer 浏览器下载成功: {meta.doi}")
                            tab.close()
                            return self._success(meta, target, url)
                        target.unlink(missing_ok=True)

                except Exception as e:
                    logger.debug(f"选择器 {selector} 失败: {e}")
                    continue

            tab.close()

            logger.warning(f"Springer 页面未找到 PDF 下载入口: {meta.doi}")
            return self._needs_manual(meta, FailureReason.PDF_NOT_FOUND, url)

        except Exception as e:
            logger.error(f"Springer 浏览器下载异常: {e}")
            target.unlink(missing_ok=True)
            return self._needs_manual(meta, FailureReason.UNKNOWN_ERROR, url)
