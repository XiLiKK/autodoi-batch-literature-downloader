"""模块7：结果统计与日志输出"""

import csv
import json
import logging
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table

from .db import Database
from .constants import TaskStatus

logger = logging.getLogger(__name__)
console = Console()


class Reporter:
    """结果报告器"""

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_all(self, db: Database):
        """生成所有报告文件"""
        self._write_results_csv(db)
        self._write_failed_csv(db)
        self._write_manual_csv(db)
        self._write_run_log(db)
        self._print_summary(db)

    def _write_results_csv(self, db: Database):
        """输出 results.csv — 全部结果"""
        path = self.output_dir / "results.csv"
        records = db.get_all_downloads()

        if not records:
            logger.info("无下载记录")
            return

        fieldnames = [
            "doi", "status", "title", "year", "journal_abbr",
            "first_author", "local_path", "source_url",
            "failure_reason", "attempt_count", "processed_at",
        ]

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)

        console.print(f"  📄 [dim]{path}[/dim] ({len(records)} 条)")

    def _write_failed_csv(self, db: Database):
        """输出 failed.csv"""
        path = self.output_dir / "failed.csv"
        records = db.get_downloads_by_status(TaskStatus.FAILED_HARD)

        fieldnames = [
            "doi", "title", "failure_reason", "source_url",
            "attempt_count", "processed_at",
        ]

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)

        console.print(f"  📄 [dim]{path}[/dim] ({len(records)} 条)")

    def _write_manual_csv(self, db: Database):
        """输出 manual_queue.csv"""
        path = self.output_dir / "manual_queue.csv"
        items = db.get_pending_manual_items()

        fieldnames = [
            "doi", "title", "publisher", "landing_url",
            "blocked_reason", "suggested_action", "resolved",
        ]

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(items)

        console.print(f"  📄 [dim]{path}[/dim] ({len(items)} 条)")

    def _write_run_log(self, db: Database):
        """输出 run_log.json"""
        path = self.output_dir / "run_log.json"
        stats = db.get_stats()

        # 失败原因统计
        failed_records = db.get_downloads_by_status(TaskStatus.FAILED_HARD)
        reason_counts: dict[str, int] = {}
        for r in failed_records:
            reason = r.get("failure_reason", "UNKNOWN")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        manual_records = db.get_downloads_by_status(TaskStatus.NEEDS_MANUAL)
        manual_reason_counts: dict[str, int] = {}
        for r in manual_records:
            reason = r.get("failure_reason", "UNKNOWN")
            manual_reason_counts[reason] = manual_reason_counts.get(reason, 0) + 1

        log = {
            "timestamp": datetime.now().isoformat(),
            "summary": stats,
            "failure_reasons": reason_counts,
            "manual_reasons": manual_reason_counts,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2, ensure_ascii=False)

        console.print(f"  📄 [dim]{path}[/dim]")

    def _print_summary(self, db: Database):
        """打印终端摘要"""
        stats = db.get_stats()
        total = sum(stats.get(s.value, 0) for s in TaskStatus)

        console.print()
        table = Table(title="📊 运行统计", show_lines=True)
        table.add_column("状态", style="bold")
        table.add_column("数量", justify="right")
        table.add_column("比例", justify="right")

        status_styles = {
            TaskStatus.SUCCESS_AUTO: ("✅ 自动成功", "green"),
            TaskStatus.SKIPPED_ALREADY_HAVE: ("⏭️  已存在跳过", "blue"),
            TaskStatus.NEEDS_MANUAL: ("🔧 待人工", "yellow"),
            TaskStatus.FAILED_HARD: ("❌ 失败", "red"),
            TaskStatus.PENDING: ("⏳ 待处理", "dim"),
        }

        for status, (label, style) in status_styles.items():
            count = stats.get(status.value, 0)
            pct = f"{count / total * 100:.1f}%" if total > 0 else "0%"
            table.add_row(f"[{style}]{label}[/{style}]", str(count), pct)

        table.add_row("[bold]总计[/bold]", f"[bold]{total}[/bold]", "100%")
        console.print(table)
        console.print()
