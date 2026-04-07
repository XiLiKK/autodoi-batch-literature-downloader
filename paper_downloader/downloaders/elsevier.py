"""Elsevier/ScienceDirect 下载策略器"""

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


class ElsevierDownloader(BrowserDownloader):
    """
    Elsevier/ScienceDirect 下载策略。
    """

    PDF_SELECTORS = [
        'text:View PDF',
        'text:Download PDF',
        'a.pdf-download',
        'a[data-track-action="download-pdf"]',
        '#pdfLink',
        'a.link-button-primary',
        'a[href*="pdfft"]',
        'a[href*="/pdf/"]',
        'a.accessbar-primary-link',
        'span.pdf-download-label',
        'a[aria-label*="Download PDF"]',
        'a.anchor.pdf-download',
        '.PdfDownload a',
        'a.download-link',
        '#downloadPdf',
    ]

    COOKIE_SELECTORS = [
        '#onetrust-accept-btn-handler',
        'button[data-cc-action="accept"]',
        '.cc-banner__button-accept',
        'button:has-text("Accept")',
        'button:has-text("Accept All Cookies")',
    ]

    def download(self, meta: PaperMetadata) -> DownloadResult:
        # 策略 1：尝试 ScienceDirect pdfft 直链
        result = self._try_direct_pdf(meta)
        if result and result.status.value == "SUCCESS_AUTO":
            return result

        # 策略 2：浏览器自动化
        return self._try_browser(meta)

    def _try_direct_pdf(self, meta: PaperMetadata) -> Optional[DownloadResult]:
        """尝试 ScienceDirect PDF 直链"""
        target = self._target_path(meta)
        urls_to_try = []

        if "sciencedirect.com" in meta.landing_url:
            pdf_url = meta.landing_url.rstrip("/") + "/pdfft"
            urls_to_try.append(pdf_url)

        if not urls_to_try:
            return None

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
                    logger.info(f"Elsevier 直链尝试: {url}")
                    resp = client.get(url)
                    ct = resp.headers.get("content-type", "")

                    if resp.status_code == 200 and "pdf" in ct.lower():
                        with open(target, "wb") as f:
                            f.write(resp.content)
                        if is_valid_pdf(target):
                            logger.info(f"Elsevier 直链成功: {meta.doi}")
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
            
            logger.info(f"Elsevier 浏览器访问: {url}")
            try:
                # 限制加载超时避免 CF 页无响应卡死
                tab.get(url, timeout=15)
            except Exception as e:
                logger.debug(f"tab.get 异常(可恢复): {e}")

            time.sleep(2)
            # 多次尝试处理 Cloudflare Turnstile (它可能会延迟加载或重载)
            for _ in range(3):
                if self._handle_cloudflare_turnstile(tab):
                    logger.info("验证完成, 额外等待重新渲染...")
                    time.sleep(5)
                    break
                time.sleep(2)

            self._dismiss_cookie_banner(tab)

            # --- 最强旁路：通过 URL PII 直接计算出 PDF 下载地址，完全无视前端 UI ---
            # 安全等待重定向完成，防止出现 '网页已断开'
            try:
                tab.wait.doc_loaded(timeout=20)
            except Exception:
                pass
                
            import re
            # 优先从原始目标 url 中提取 PII，否则从当前 URL 提取
            pii_match = re.search(r'pii/([a-zA-Z0-9X\-]+)', url)
            if not pii_match:
                try:
                    pii_match = re.search(r'pii/([a-zA-Z0-9X\-]+)', tab.url)
                except Exception:
                    pass

            if pii_match:
                pii = pii_match.group(1)
                pdf_url = f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft?download=true"
                logger.info(f"提取到 PII ({pii})，直接发包极速下载 PDF: {pdf_url}")
                
                tab.set.download_path(str(target.parent))
                tab.set.download_file_name(target.name)
                
                # 使用极端短超时，防止无限等待文件流导致的死锁挂起，且屏蔽挂起或网页断开的异常
                try:
                    # 原生 JS 触发有可能会被断开连接打断，优先 JS
                    tab.run_js(f'window.location.href = "{pdf_url}";')
                except Exception as e:
                    logger.debug(f"JS 注入下载被阻断: {e}，回退到短超时直链获取...")
                    try:
                        tab.get(pdf_url, timeout=3)
                    except Exception:
                        pass
                
                # 无论 get/run_js 怎么异常，只要下载能开始就不管
                tab.wait.download_begin(timeout=15)
                
                logger.info("已发包！开始监控本地文件写入进度，请耐心等待 (由于 Elsevier 限制，大文件可能需要 1~3 分钟)...")
                wait_count = 0
                max_wait = 300  # 强制最大等待 5 分钟
                while wait_count < max_wait:
                    if target.exists() and is_valid_pdf(target):
                        logger.info("PDF 直达下载成功!")
                        tab.close()
                        return self._success(meta, target, pdf_url)
                    time.sleep(1)
                    wait_count += 1
                    if wait_count % 15 == 0:
                        logger.debug(f"正在等待文件下载完成... ({wait_count}/{max_wait}s)")

            # 如果旁路失败，回退到以前的检测逻辑 (Captcha -> DOM 提取 -> 物理点击)
            if self._check_captcha(tab):
                tab.close()
                return self._needs_manual(meta, FailureReason.CAPTCHA_TRIGGERED, url)

            if self._check_access_denied(tab):
                tab.close()
                return self._needs_manual(meta, FailureReason.ACCESS_DENIED, url)

            # Elsevier 页面可能由于沉重的框架导致加载缓慢，这里给足时间寻找
            target_el = None
            for _ in range(5):  # 尝试 5 次，总计约 15-25 秒
                for selector in self.PDF_SELECTORS:
                    try:
                        # 不再强制加 css:前缀，如果原生自带语法
                        s = f"css:{selector}" if not selector.startswith(('xpath:', '@', 'text=', 'text:')) else selector
                        el = tab.ele(s, timeout=3)
                        if el:
                            target_el = el
                            logger.info(f"Elsevier 找到 PDF 按钮: {selector}")
                            break
                    except Exception:
                        pass
                if target_el:
                    break
                time.sleep(2)
                
            if target_el:
                try:
                    # 尝试捕获带有 href 的 A 标签，拿到真实的跳转链
                    parent_a = target_el if target_el.tag == 'a' else target_el.parent('tag:a')
                    href = parent_a.attr('href') if parent_a else None
                    if not href:
                        href = target_el.attr('href')

                    if href and ('pdf' in href.lower() or 'pdfft' in href.lower() or 'download' in href.lower()):
                        # 拼全 URL
                        if href.startswith('/'):
                            from urllib.parse import urlparse
                            parsed = urlparse(tab.url)
                            full_href = f"{parsed.scheme}://{parsed.netloc}{href}"
                        else:
                            full_href = href
                            
                        logger.info(f"提取到 PDF 原生链接，防拦截直采: {full_href}")
                        result = self._download_from_url(full_href, target, meta, cookies=tab.cookies(as_dict=True))
                        if result and result.status.value == "SUCCESS_AUTO":
                            tab.close()
                            return result

                    # 如果拿不到真实的直达 URL，再退化成物理点击
                    tab.set.download_path(str(target.parent))
                    tab.set.download_file_name(target.name)
                    # 强力触发节点
                    try:
                        if parent_a:
                            parent_a.click()
                        else:
                            target_el.click()
                    except Exception:
                        if parent_a:
                            parent_a.click(by_js=True)
                        else:
                            target_el.click(by_js=True)
                    
                    tab.wait.download_begin(timeout=10)
                    
                    # 等待文件落地
                    wait_count = 0
                    while wait_count < self.timeout:
                        if target.exists() and is_valid_pdf(target):
                            break
                        time.sleep(1)
                        wait_count += 1
                        
                    if target.exists() and is_valid_pdf(target):
                        logger.info(f"Elsevier 下载成功: {meta.doi}")
                        tab.close()
                        return self._success(meta, target, url)
                    
                    target.unlink(missing_ok=True)
                except Exception as e:
                    logger.debug(f"下载事件失败: {e}")
                    new_url = getattr(tab, 'url', '')
                    if getattr(tab, 'title', '') and ".pdf" in str(new_url).lower():
                        result = self._download_from_url(new_url, target, meta, cookies=tab.cookies(as_dict=True))
                        if result and result.status.value == "SUCCESS_AUTO":
                            tab.close()
                            return result

            tab.close()
            logger.warning(f"Elsevier 未找到 PDF 下载入口: {meta.doi}")
            return self._needs_manual(meta, FailureReason.PDF_NOT_FOUND, url)

        except Exception as e:
            logger.error(f"Elsevier 浏览器异常: {e}")
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

    def _check_captcha(self, tab) -> bool:
        captcha_indicators = [
            'css:iframe[src*="recaptcha"]',
            'css:.g-recaptcha',
        ]
        for sel in captcha_indicators:
            try:
                el = tab.ele(sel, timeout=0.5)
                if el:
                    logger.warning("检测到 Google rcVerify 类型的复杂验证码")
                    return True
            except Exception:
                continue
        return False

    def _check_access_denied(self, tab) -> bool:
        try:
            content = tab.html.lower()
            denied_indicators = [
                "access denied",
                "institutional access",
                "purchase this article",
                "get access",
                "sign in to access",
            ]
            for indicator in denied_indicators:
                if indicator in content:
                    for sel in self.PDF_SELECTORS[:3]:
                        btn = tab.ele(f"css:{sel}", timeout=0.5)
                        if btn:
                            return False
                    logger.warning(f"检测到访问限制: {indicator}")
                    return True
        except Exception:
            pass
        return False
