"""主编排器 — 串联所有模块的核心流程"""

import logging
import time
from pathlib import Path

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .config import load_config, get_email
from .csv_reader import read_doi_csv, is_valid_doi
from .metadata import MetadataResolver
from .db import Database
from .dedup import check_duplicate
from .router import route_download
from .constants import TaskStatus, FailureReason, DownloadRoute
from .models import PaperMetadata, DownloadResult, ManualQueueItem
from .downloaders import (
    OADirectDownloader, SpringerDownloader,
    ElsevierDownloader, WileyDownloader, GenericDownloader,
    SciHubDownloader,
)
from .reporter import Reporter

logger = logging.getLogger(__name__)
console = Console()


class Orchestrator:
    """主编排器"""

    def __init__(self, csv_path: str, config_path: str | None = None,
                 project_root: str | None = None):
        self.csv_path = csv_path
        self.cfg = load_config(config_path, project_root)
        self.email = get_email(self.cfg)

        # 路径
        self.download_dir = self.cfg["download"]["output_dir"]
        self.db_path = self.cfg["database"]["path"]
        self.output_dir = self.cfg["logging"]["output_dir"]

        # 配置
        self.delay = self.cfg["download"].get("delay_between_downloads", 3)
        self.page_timeout = self.cfg["download"].get("page_timeout", 60)
        self.download_timeout = self.cfg["download"].get("download_timeout", 120)
        self.headless = self.cfg["download"].get("headless", False)
        self.max_title_length = self.cfg["naming"].get("max_title_length", 80)

        # 初始化组件
        self.db = Database(self.db_path)
        self.resolver = MetadataResolver(
            email=self.email,
            timeout=self.cfg["api"].get("request_timeout", 30),
            max_retries=self.cfg["api"].get("max_retries", 3),
        )

        # 下载器 (懒初始化)
        self._downloaders: dict[DownloadRoute, object] = {}
        # 共享 DrissionPage 实例
        self._browser = None

    def _ensure_browser(self):
        """确保共享浏览器已启动"""
        if self._browser is None:
            from DrissionPage import ChromiumPage, ChromiumOptions
            co = ChromiumOptions()
            # 总是关闭隐藏模式，因为 Turnstile 必杀 headless，使用脱屏技术躲避焦点
            co.headless(False)
            
            # 使用脱屏渲染：将浏览器窗口强制推离屏幕可见区域 (x, y)，既避开检测又不打扰用户
            co.set_argument('--window-position=-3000,-3000')
            co.set_argument('--window-size=1200,800')

            co.set_argument('--disable-blink-features=AutomationControlled')
            co.set_argument('--no-sandbox')
            co.set_argument('--disable-infobars')
            
            # 强制拦截所有 PDF 到本地，禁止浏览器内置预览查看器
            co.set_pref('plugins.always_open_pdf_externally', True)
            
            self._browser = ChromiumPage(addr_or_opts=co)
            logger.info("共享 DrissionPage 浏览器已启动")
        return self._browser

    def _get_downloader(self, route: DownloadRoute):
        """获取或创建下载器"""
        if route not in self._downloaders:
            common_kwargs = {
                "download_dir": self.download_dir,
                "timeout": self.download_timeout,
            }
            browser_kwargs = {
                **common_kwargs,
                "page_timeout": self.page_timeout,
                "headless": self.headless,
                "browser": self._ensure_browser(),  # 传入共享浏览器
            }

            if route == DownloadRoute.OA_DIRECT:
                self._downloaders[route] = OADirectDownloader(**common_kwargs)
            elif route == DownloadRoute.SPRINGER:
                self._downloaders[route] = SpringerDownloader(**browser_kwargs)
            elif route == DownloadRoute.ELSEVIER:
                self._downloaders[route] = ElsevierDownloader(**browser_kwargs)
            elif route == DownloadRoute.WILEY:
                self._downloaders[route] = WileyDownloader(**browser_kwargs)
            elif route == DownloadRoute.GENERIC:
                self._downloaders[route] = GenericDownloader(**browser_kwargs)
            elif route == DownloadRoute.SCI_HUB:
                scihub_mirrors = self.cfg.get("download", {}).get("scihub_mirrors")
                self._downloaders[route] = SciHubDownloader(scihub_mirrors=scihub_mirrors, **browser_kwargs)

        return self._downloaders[route]

    def run(self):
        """执行主流程"""
        console.print("\n[bold cyan]DOI 批量文献下载工具[/bold cyan]")
        console.print(f"  CSV: {self.csv_path}")
        console.print(f"  输出: {self.download_dir}")
        console.print(f"  数据库: {self.db_path}")
        console.print()

        # Step 1: 读取 CSV
        console.print("[bold]Step 1: 读取 CSV...[/bold]")
        try:
            tasks = read_doi_csv(self.csv_path)
        except Exception as e:
            console.print(f"[red]CSV 读取失败: {e}[/red]")
            return
        console.print(f"  共 {len(tasks)} 个唯一 DOI\n")

        if not tasks:
            console.print("[yellow]没有需要处理的 DOI[/yellow]")
            return

        # 过滤无效 DOI
        valid_tasks = []
        invalid_count = 0
        for task in tasks:
            if is_valid_doi(task.doi_normalized):
                valid_tasks.append(task)
            else:
                invalid_count += 1
                logger.warning(f"无效 DOI: {task.doi_raw}")
                self.db.upsert_paper(PaperMetadata(doi=task.doi_normalized))
                self.db.record_download(DownloadResult(
                    doi=task.doi_normalized,
                    status=TaskStatus.FAILED_HARD,
                    failure_reason=FailureReason.INVALID_DOI,
                ))

        if invalid_count:
            console.print(f"  [yellow]{invalid_count} 个无效 DOI 已跳过[/yellow]\n")

        # 主处理循环
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            main_task = progress.add_task(
                "处理论文...", total=len(valid_tasks)
            )

            for i, task in enumerate(valid_tasks):
                doi = task.doi_normalized
                progress.update(main_task, description=f"[{i+1}/{len(valid_tasks)}] {doi[:50]}")

                try:
                    self._process_one(doi)
                except Exception as e:
                    logger.error(f"处理失败: {doi} - {e}")
                    self.db.record_download(DownloadResult(
                        doi=doi,
                        status=TaskStatus.FAILED_HARD,
                        failure_reason=FailureReason.UNKNOWN_ERROR,
                    ))

                progress.advance(main_task)

                # 下载间隔
                if i < len(valid_tasks) - 1:
                    time.sleep(self.delay)

        # 清理
        self._cleanup()

        # 输出报告
        console.print("\n[bold]生成报告...[/bold]")
        reporter = Reporter(self.output_dir)
        reporter.generate_all(self.db)

        # 去除误导性的数据库历史人工提示

    def _process_one(self, doi: str):
        """处理单个 DOI"""
        # Step 2: 检查重复
        if check_duplicate(doi, self.db, self.download_dir):
            self.db.record_download(DownloadResult(
                doi=doi,
                status=TaskStatus.SKIPPED_ALREADY_HAVE,
            ))
            logger.info(f"跳过已存在: {doi}")
            return

        # Step 3: 获取元数据
        try:
            meta = self.resolver.resolve(doi, self.max_title_length)
        except Exception as e:
            logger.error(f"元数据解析失败: {doi} - {e}")
            self.db.upsert_paper(PaperMetadata(doi=doi))
            self.db.record_download(DownloadResult(
                doi=doi,
                status=TaskStatus.FAILED_HARD,
                failure_reason=FailureReason.METADATA_NOT_FOUND,
            ))
            return

        # 保存元数据
        self.db.upsert_paper(meta)
        logger.info(f"元数据: {meta.year} | {meta.journal_abbr} | {meta.first_author} | {meta.title[:50]}")

        # Step 4: 路由
        route = route_download(meta)

        # Step 5: 执行下载
        downloader = self._get_downloader(route)
        result = downloader.download(meta)

        # Step 5.5: 引入 Sci-Hub 兜底
        if result.status == TaskStatus.NEEDS_MANUAL and self.cfg["download"].get("use_scihub_fallback", False):
            logger.info(f"原策略失败，尝试 Sci-Hub 兜底: {doi}")
            scihub_downloader = self._get_downloader(DownloadRoute.SCI_HUB)
            scihub_result = scihub_downloader.download(meta)
            
            if scihub_result.status == TaskStatus.SUCCESS_AUTO:
                result = scihub_result
                logger.info(f"Sci-Hub 挽救成功: {doi}")
            else:
                logger.warning(f"Sci-Hub 兜底也失败了: {doi}")

        # Step 6: 记录结果
        self.db.record_download(result)

        if result.status == TaskStatus.NEEDS_MANUAL:
            self.db.add_to_manual_queue(ManualQueueItem(
                doi=doi,
                title=meta.title,
                publisher=meta.publisher,
                landing_url=meta.landing_url,
                blocked_reason=result.failure_reason.value if result.failure_reason else "UNKNOWN",
                suggested_action="在浏览器中手动访问并下载 PDF",
            ))

        status_label = {
            TaskStatus.SUCCESS_AUTO: "SUCCESS_AUTO",
            TaskStatus.NEEDS_MANUAL: "NEEDS_MANUAL",
            TaskStatus.FAILED_HARD: "FAILED_HARD",
        }
        label = status_label.get(result.status, result.status.value)
        logger.info(f"{label}: {doi}")

    def _cleanup(self):
        """清理资源"""
        self.resolver.close()
        # 先关闭下载器（它们不会关闭共享浏览器）
        for dl in self._downloaders.values():
            try:
                if hasattr(dl, "close"):
                    dl.close()
            except Exception:
                pass
        # 然后关闭共享浏览器
        if self._browser:
            try:
                self._browser.quit()
            except Exception:
                pass
            self._browser = None

    def close(self):
        """关闭所有资源"""
        self._cleanup()
        self.db.close()
