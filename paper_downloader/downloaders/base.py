"""下载器基类"""

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import PaperMetadata, DownloadResult
from ..constants import TaskStatus, FailureReason

logger = logging.getLogger(__name__)


class BaseDownloader(ABC):
    """所有下载器的基类"""

    def __init__(self, download_dir: str | Path, timeout: int = 120):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout

    @abstractmethod
    def download(self, meta: PaperMetadata) -> DownloadResult:
        """
        执行下载。
        
        Returns:
            DownloadResult 包含状态和文件路径
        """
        ...

    def close(self):
        """清理资源"""
        pass

    def _target_path(self, meta: PaperMetadata) -> Path:
        """计算目标文件路径"""
        return self.download_dir / meta.target_filename

    def _success(self, meta: PaperMetadata, path: Path, source_url: str,
                 attempt: int = 1) -> DownloadResult:
        return DownloadResult(
            doi=meta.doi,
            status=TaskStatus.SUCCESS_AUTO,
            local_path=str(path),
            source_url=source_url,
            attempt_count=attempt,
        )

    def _needs_manual(self, meta: PaperMetadata, reason: FailureReason,
                      source_url: str = "", attempt: int = 1) -> DownloadResult:
        return DownloadResult(
            doi=meta.doi,
            status=TaskStatus.NEEDS_MANUAL,
            source_url=source_url,
            failure_reason=reason,
            attempt_count=attempt,
        )

    def _failed(self, meta: PaperMetadata, reason: FailureReason,
                source_url: str = "", attempt: int = 1) -> DownloadResult:
        return DownloadResult(
            doi=meta.doi,
            status=TaskStatus.FAILED_HARD,
            source_url=source_url,
            failure_reason=reason,
            attempt_count=attempt,
        )


class BrowserDownloader(BaseDownloader):
    """需要浏览器的下载器基类 — 共享 Playwright 实例"""

    # 子类覆盖
    COOKIE_SELECTORS: list[str] = [
        '#onetrust-accept-btn-handler',
        'button[data-cc-action="accept"]',
        'button:has-text("Accept")',
        'button:has-text("Accept All")',
    ]

    def __init__(self, download_dir: str | Path, timeout: int = 120,
                 page_timeout: int = 60, headless: bool = False,
                 browser=None):
        super().__init__(download_dir, timeout)
        self.page_timeout = page_timeout
        self.headless = headless
        # 共享浏览器实例（由 orchestrator 传入属于 ChromiumPage）
        self._shared_browser = browser
        self._own_browser = None

    @property
    def browser(self):
        """获取浏览器实例"""
        if self._shared_browser:
            return self._shared_browser
        if self._own_browser is None:
            from DrissionPage import ChromiumPage, ChromiumOptions
            co = ChromiumOptions()
            if self.headless:
                co.headless()
            co.set_argument('--disable-blink-features=AutomationControlled')
            co.set_argument('--disable-infobars')
            self._own_browser = ChromiumPage(addr_or_opts=co)
            # 设置页面超时
            self._own_browser.set.timeouts(page_load=self.page_timeout)
        return self._own_browser

    def set_browser(self, browser):
        """设置共享浏览器"""
        self._shared_browser = browser

    def close(self):
        """只关闭自有实例，不关闭共享的"""
        if self._own_browser:
            self._own_browser.quit()
            self._own_browser = None

    def _dismiss_cookie_banner(self, tab):
        """尝试关闭 cookie 弹窗"""
        for sel in self.COOKIE_SELECTORS:
            try:
                btn = tab.ele(sel)
                if btn:
                    btn.click()
                    time.sleep(0.5)
                    return
            except Exception:
                continue

    def create_stealth_tab(self):
        """创建并返回一个启用了 stealth 的独立标签页"""
        tab = self.browser.new_tab()
        return tab

    def _handle_cloudflare_turnstile(self, tab) -> bool:
        """尝试自动化处理 Cloudflare Turnstile 人机验证"""
        try:
            frames = tab.get_frames()
            frames.insert(0, tab)
            
            for frame in frames:
                try:
                    # 获取该 frame/tab 下的可能靶点
                    target = frame.ele('确认您是真人', timeout=0.1) or \
                             frame.ele('Verify you are human', timeout=0.1) or \
                             frame.ele('css:.mark', timeout=0.1) or \
                             frame.ele('css:input[type="checkbox"]', timeout=0.1)
                    
                    if target:
                        logger.warning("拦截到 Cloudflare 验证盾，尝试第一种物理点击...")
                        time.sleep(1.5)
                        
                        try:
                            # 尝试真实鼠标坐标点击
                            target.click(by_js=False)
                        except Exception as e:
                            logger.error(f"物理点击失败, 尝试 JS 强制点击: {e}")
                            target.click(by_js=True)
                            
                        logger.info("已完成点击 CF 验证框！等待防火墙放行...")
                        time.sleep(4)
                        return True
                except Exception as inner_e:
                    logger.debug(f"内部点击循环报错: {inner_e}")
                    continue
        except Exception as e:
            logger.debug(f"CF Turnstile 处理异常: {e}")
            
        return False
