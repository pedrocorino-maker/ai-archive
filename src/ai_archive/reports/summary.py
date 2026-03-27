"""AI Archive — Rich summary tables for runs, topics, and Drive sync."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..models import CrawlRun, TopicCluster

console = Console()


def print_run_summary(run: CrawlRun) -> None:
    """Print a rich table summarizing a CrawlRun."""
    table = Table(title=f"Crawl Run: {run.run_id}", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    provider_label = run.provider.value if run.provider else "all"
    table.add_row("Provider", provider_label)
    table.add_row("Auth Mode", run.auth_mode.value)
    table.add_row("Started At", run.started_at.isoformat() if run.started_at else "-")
    table.add_row("Finished At", run.finished_at.isoformat() if run.finished_at else "-")
    if run.started_at and run.finished_at:
        elapsed = (run.finished_at - run.started_at).total_seconds()
        table.add_row("Elapsed (s)", f"{elapsed:.1f}")

    if run.harvest_discovered > 0:
        table.add_row("[bold]— Phase 1: Sidebar Harvest —[/bold]", "")
        table.add_row("  Conversations Harvested", str(run.harvest_discovered))
        table.add_row("  Harvest Duration (min)", f"{run.harvest_duration_minutes:.1f}")
        end_reason_display = {
            "max_minutes_reached": "max minutes reached",
            "expected_count_reached": "expected count reached",
            "min_duration_stagnation": "min duration + stagnation",
        }.get(run.harvest_end_reason, run.harvest_end_reason or "-")
        table.add_row("  Harvest End Reason", end_reason_display)
        table.add_row("[bold]— Phase 2: Extraction —[/bold]", "")

    table.add_row("Conversations Found", str(run.conversations_found))
    table.add_row("Conversations New", str(run.conversations_new))
    table.add_row("Conversations Updated", str(run.conversations_updated))
    table.add_row("Conversations Failed", str(run.conversations_failed))
    table.add_row("Topics Consolidated", str(run.topics_consolidated))
    table.add_row("Drive Uploads", str(run.drive_uploads))

    status_label = "[green]SUCCESS[/green]" if run.success else "[red]FAILED[/red]"
    table.add_row("Status", status_label)
    if run.error_summary:
        table.add_row("Error Summary", run.error_summary[:100])

    console.print(table)


def print_topic_summary(topics: list[TopicCluster]) -> None:
    """Print a rich table summarizing TopicClusters."""
    table = Table(title=f"Topic Clusters ({len(topics)} total)", show_header=True, header_style="bold magenta")
    table.add_column("Slug", style="cyan")
    table.add_column("Title")
    table.add_column("Convs", justify="right")
    table.add_column("Providers")
    table.add_column("Tags")

    for topic in topics:
        providers_str = ", ".join(
            f"{k}:{v}" for k, v in topic.provider_counts.items()
        )
        tags_str = ", ".join(topic.tags[:4])
        table.add_row(
            topic.topic_slug,
            topic.topic_title[:60],
            str(len(topic.conversation_ids)),
            providers_str,
            tags_str,
        )

    console.print(table)


def print_drive_summary(stats: dict) -> None:
    """Print a rich summary of Drive sync stats."""
    table = Table(title="Drive Sync Summary", show_header=True, header_style="bold blue")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")

    table.add_row("Files Created", str(stats.get("files_created", 0)))
    table.add_row("Files Updated", str(stats.get("files_updated", 0)))
    table.add_row("Files Skipped", str(stats.get("files_skipped", 0)))

    total = sum(stats.get(k, 0) for k in ("files_created", "files_updated", "files_skipped"))
    table.add_row("[bold]Total Processed[/bold]", str(total))

    console.print(table)
