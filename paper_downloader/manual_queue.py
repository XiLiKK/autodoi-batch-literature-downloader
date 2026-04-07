"""模块6：人工确认队列交互"""

import logging
import webbrowser

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt

from .db import Database
from .models import ManualQueueItem

logger = logging.getLogger(__name__)
console = Console()


def process_manual_queue(db: Database):
    """
    交互式处理人工确认队列。
    
    逐个打开待处理页面，让用户决定处理结果。
    """
    items = db.get_pending_manual_items()

    if not items:
        console.print("\n[green]✓ 人工队列为空，无需处理。[/green]\n")
        return

    console.print(f"\n[bold yellow]📋 共 {len(items)} 条待处理[/bold yellow]\n")

    # 先展示列表
    table = Table(title="待处理项目", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("DOI", style="cyan", max_width=40)
    table.add_column("标题", max_width=40)
    table.add_column("出版商", max_width=15)
    table.add_column("原因", style="yellow", max_width=25)

    for i, item in enumerate(items, 1):
        table.add_row(
            str(i),
            item.get("doi", ""),
            (item.get("title") or "")[:40],
            (item.get("publisher") or "")[:15],
            item.get("blocked_reason", ""),
        )
    console.print(table)
    console.print()

    # 逐个处理
    for i, item in enumerate(items, 1):
        console.rule(f"[bold] 第 {i}/{len(items)} 条 [/bold]")
        console.print(f"  DOI:    [cyan]{item.get('doi', '')}[/cyan]")
        console.print(f"  标题:   {item.get('title', '未知')}")
        console.print(f"  出版商: {item.get('publisher', '未知')}")
        console.print(f"  URL:    [link]{item.get('landing_url', '')}[/link]")
        console.print(f"  原因:   [yellow]{item.get('blocked_reason', '')}[/yellow]")
        console.print(f"  建议:   {item.get('suggested_action', '')}")
        console.print()

        action = Prompt.ask(
            "操作",
            choices=["open", "skip", "done", "fail", "quit"],
            default="open",
        )

        if action == "open":
            url = item.get("landing_url", "")
            if url:
                console.print(f"  [dim]正在打开浏览器...[/dim]")
                webbrowser.open(url)

                # 等待用户处理完
                result = Prompt.ask(
                    "  处理完了吗?",
                    choices=["done", "fail", "skip"],
                    default="done",
                )
                if result == "done":
                    db.resolve_manual_item(item["id"], resolved=True)
                    console.print("  [green]✓ 已标记为完成[/green]")
                elif result == "fail":
                    db.resolve_manual_item(item["id"], resolved=True)
                    console.print("  [red]✗ 已标记为失败[/red]")
                else:
                    console.print("  [dim]跳过[/dim]")
            else:
                console.print("  [red]无 URL 可打开[/red]")

        elif action == "done":
            db.resolve_manual_item(item["id"], resolved=True)
            console.print("  [green]✓ 已标记为完成[/green]")

        elif action == "fail":
            db.resolve_manual_item(item["id"], resolved=True)
            console.print("  [red]✗ 已标记为失败[/red]")

        elif action == "skip":
            console.print("  [dim]跳过[/dim]")

        elif action == "quit":
            console.print("\n[yellow]退出人工队列处理[/yellow]")
            break

        console.print()

    # 最终统计
    remaining = db.get_pending_manual_items()
    console.print(f"\n[bold]处理完毕。剩余待处理: {len(remaining)}[/bold]\n")
