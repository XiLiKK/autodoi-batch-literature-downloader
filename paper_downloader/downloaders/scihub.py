"""Sci-Hub 兜底下载策略器"""

import logging
import time
from pathlib import Path

from .base import BrowserDownloader
from ..models import PaperMetadata, DownloadResult
from ..constants import FailureReason
from ..validator import is_valid_pdf

logger = logging.getLogger(__name__)


class SciHubDownloader(BrowserDownloader):
    """
    Sci-Hub 兜底下载策略。
    在官方渠道全部失败 (NEEDS_MANUAL) 时启用。
    """

    def __init__(self, scihub_mirrors: list[str] | None = None, **kwargs):
        super().__init__(**kwargs)
        self.mirrors = scihub_mirrors or ["https://sci-hub.se", "https://sci-hub.ru", "https://sci-hub.st"]

    def download(self, meta: PaperMetadata) -> DownloadResult:
        doi = meta.doi
        target = self._target_path(meta)

        # 尝试每个镜像
        for mirror in self.mirrors:
            mirror = mirror.rstrip("/")
            url = f"{mirror}/{doi}"
            logger.info(f"尝试 Sci-Hub 镜像: {url}")

            try:
                tab = self.create_stealth_tab()

                try:
                    tab.get(url)
                except Exception as e:
                    logger.debug(f"Sci-Hub 访问异常: {e}")
                    tab.close()
                    continue

                time.sleep(2)

                # 执行 JS 更可靠地提取真正的 PDF 链接
                pdf_url = tab.run_js("""
                    let url = null;
                    // 1. 尝试 embed 或 object
                    let obj = document.querySelector('embed[type="application/pdf"], object[type="application/pdf"]');
                    if (obj) {
                        url = obj.src || obj.data;
                        if (url) return url;
                    }
                    // 2. 尝试 iframe
                    let iframe = document.querySelector('iframe');
                    if (iframe && iframe.src && iframe.src.includes('.pdf')) {
                        return iframe.src;
                    }
                    // 3. 尝试所有 a 标签里的 storage 或者 pdf 链接
                    let links = document.querySelectorAll('a[href]');
                    for (let a of links) {
                        if (a.href.includes('/storage/') || a.href.includes('.pdf')) {
                            if (!a.href.includes('mailto')) return a.href;
                        }
                    }
                    return null;
                """)

                if pdf_url:
                    # 修正相对 URL (如 //domain/path 转成 https://domain/path)
                    if pdf_url.startswith("//"):
                        pdf_url = "https:" + pdf_url
                    elif pdf_url.startswith("/"):
                        pdf_url = mirror + pdf_url

                    logger.info(f"Sci-Hub 找到真实地址: {pdf_url}")

                    # 为了避免浏览器同源策略有时阻止下载，直接开一个新标签页去下载这个真实 URL
                    try:
                        tab.set.download_path(str(target.parent))
                        tab.set.download_file_name(target.name)
                        
                        js = f"""
                        const a = document.createElement('a');
                        a.href = '{pdf_url}';
                        a.download = '';
                        document.body.appendChild(a);
                        a.click();
                        """
                        tab.run_js(js)
                        
                        tab.wait.download_begin(timeout=10)
                        
                        wait_count = 0
                        while wait_count < self.timeout:
                            if target.exists() and is_valid_pdf(target):
                                break
                            time.sleep(1)
                            wait_count += 1
                            
                        if target.exists() and is_valid_pdf(target):
                            logger.info(f"Sci-Hub 下载成功: {doi}")
                            tab.close()
                            return self._success(meta, target, url)
                        target.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug(f"Sci-Hub 浏览器下载事件失败: {e}")
                        # 尝试纯 HTTP 请求兜底
                        import httpx
                        cookies_dict = {c['name']: c['value'] for c in tab.cookies()} if tab.cookies() else None
                        client = httpx.Client(timeout=self.timeout, follow_redirects=True, verify=False, cookies=cookies_dict)
                        try:
                            resp = client.get(pdf_url)
                            if resp.status_code == 200:
                                with open(target, "wb") as f:
                                    f.write(resp.content)
                                if is_valid_pdf(target):
                                    logger.info(f"Sci-Hub HTTP 下载成功: {doi}")
                                    client.close()
                                    tab.close()
                                    return self._success(meta, target, pdf_url)
                                target.unlink(missing_ok=True)
                        except Exception as ex:
                            logger.debug(f"Sci-Hub HTTP 下载失败: {ex}")
                        finally:
                            client.close()
                else:
                    logger.debug(f"Sci-Hub 页面未找到 PDF 链接: {mirror}")

                tab.close()
            
            except Exception as e:
                logger.error(f"Sci-Hub 镜像 {mirror} 异常: {e}")
                target.unlink(missing_ok=True)
                continue

        logger.warning(f"所有 Sci-Hub 镜像均未成功: {doi}")
        return self._needs_manual(meta, FailureReason.PDF_NOT_FOUND)
