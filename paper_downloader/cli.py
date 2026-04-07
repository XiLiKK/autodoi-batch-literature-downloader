"""CLI 命令定义"""

import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

console = Console()


def setup_logging(level: str = "INFO"):
    """配置日志"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """DOI 批量文献下载工具"""
    pass


@cli.command()
@click.option("--csv", "-c", required=True,
              help="输入 CSV 文件路径 (必须包含 DOI 列)")
@click.option("--output", "-o", default=None,
              help="PDF 下载目录 (默认: output/downloads)")
@click.option("--config", default=None,
              help="配置文件路径 (默认: config/settings.yaml)")
@click.option("--root", default=None,
              help="项目根目录 (默认: 自动检测)")
@click.option("--log-level", default="INFO",
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
              help="日志等级")
def run(csv, output, config, root, log_level):
    """执行批量下载"""
    setup_logging(log_level)

    # 确定项目根目录
    if root is None:
        root = str(Path(__file__).resolve().parent.parent)

    from .orchestrator import Orchestrator

    try:
        orch = Orchestrator(
            csv_path=csv,
            config_path=config,
            project_root=root,
        )

        # 覆盖输出目录
        if output:
            orch.download_dir = str(Path(output).resolve())
            Path(output).mkdir(parents=True, exist_ok=True)

        orch.run()

    except ValueError as e:
        console.print(f"[red]配置错误: {e}[/red]")
        sys.exit(1)
    except FileNotFoundError as e:
        console.print(f"[red]文件未找到: {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]用户取消[/yellow]")
        sys.exit(130)


@cli.command()
@click.option("--config", default=None, help="配置文件路径")
@click.option("--root", default=None, help="项目根目录")
def manual(config, root):
    """处理人工确认队列"""
    setup_logging("INFO")

    if root is None:
        root = str(Path(__file__).resolve().parent.parent)

    from .config import load_config
    from .db import Database
    from .manual_queue import process_manual_queue

    cfg = load_config(config, root)
    db = Database(cfg["database"]["path"])

    try:
        process_manual_queue(db)
    finally:
        db.close()


@cli.command()
@click.option("--config", default=None, help="配置文件路径")
@click.option("--root", default=None, help="项目根目录")
def stats(config, root):
    """查看下载统计"""
    setup_logging("WARNING")

    if root is None:
        root = str(Path(__file__).resolve().parent.parent)

    from .config import load_config
    from .db import Database
    from .reporter import Reporter

    cfg = load_config(config, root)
    db = Database(cfg["database"]["path"])

    try:
        reporter = Reporter(cfg["logging"]["output_dir"])
        reporter._print_summary(db)
    finally:
        db.close()


@cli.command()
@click.option("--csv", "-c", required=True,
              help="输入 CSV 文件路径")
@click.option("--config", default=None, help="配置文件路径")
@click.option("--root", default=None, help="项目根目录")
@click.option("--log-level", default="INFO",
              type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]))
def retry(csv, config, root, log_level):
    """重试失败的 DOI"""
    setup_logging(log_level)

    if root is None:
        root = str(Path(__file__).resolve().parent.parent)

    from .config import load_config
    from .db import Database
    from .constants import TaskStatus

    cfg = load_config(config, root)
    db = Database(cfg["database"]["path"])

    # 获取失败记录
    failed = db.get_downloads_by_status(TaskStatus.FAILED_HARD)
    if not failed:
        console.print("[green]没有失败记录需要重试[/green]")
        db.close()
        return

    console.print(f"[yellow]找到 {len(failed)} 条失败记录[/yellow]")

    # 删除失败记录，重新跑
    for r in failed:
        doi = r["doi"]
        db.conn.execute("DELETE FROM downloads WHERE doi = ? AND status = ?",
                        (doi, TaskStatus.FAILED_HARD))
    db.conn.commit()
    db.close()

    # 重新运行
    from .orchestrator import Orchestrator
    orch = Orchestrator(csv_path=csv, config_path=config, project_root=root)
    orch.run()
