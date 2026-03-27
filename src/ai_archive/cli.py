"""AI Archive — Typer CLI application."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from . import __version__

console = Console()
app = typer.Typer(
    name="ai-archive",
    help="Capture and archive AI conversations.",
    add_completion=False,
)

# Sub-app for auth subcommands
auth_app = typer.Typer(name="auth", help="Authentication management.")
app.add_typer(auth_app, name="auth")

# Sub-app for drive subcommands
drive_app = typer.Typer(name="drive", help="Google Drive operations.")
app.add_typer(drive_app, name="drive")

# Sub-app for sync
sync_app = typer.Typer(name="sync", help="Sync all pipelines.")
app.add_typer(sync_app, name="sync")

# Sub-app for manual imports
import_app = typer.Typer(name="import", help="Manual import commands.")
app.add_typer(import_app, name="import")


def _get_settings():
    from .config import get_settings
    return get_settings()


def _init_logging(settings) -> None:
    from .logging_config import setup_logging
    setup_logging(
        logs_dir=settings.logs_dir,
        level=settings.log_level,
        json_logs=settings.json_logs,
        human_logs=settings.human_logs,
    )


def _get_db(settings):
    from .db import init_db
    return init_db(settings.db_file)


def _print_run_id() -> None:
    from .logging_config import get_run_id
    console.print(f"[dim]run_id: {get_run_id()}[/dim]")


def _error_exit(msg: str) -> None:
    console.print(Panel(f"[bold red]{msg}[/bold red]", title="Error"))
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

@app.command()
def doctor() -> None:
    """Run environment health checks."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()
    from .reports.doctor import print_doctor_report
    ok = print_doctor_report(settings)
    if not ok:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

@auth_app.command("browser")
def auth_browser() -> None:
    """Launch managed profile browser, wait for manual login, save state."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()
    console.print("[cyan]Launching browser for manual login...[/cyan]")

    async def _run() -> None:
        from .auth.browser_session import BrowserSession
        from .models import Provider, AuthMode
        import os

        # Force managed_profile mode for auth setup
        object.__setattr__(settings, "auth_mode", AuthMode.MANAGED_PROFILE.value)

        async with BrowserSession(settings=settings) as session:
            # Determine which providers to authenticate
            providers = []
            if settings.chatgpt_enabled:
                providers.append(Provider.CHATGPT)
            if settings.gemini_enabled:
                providers.append(Provider.GEMINI)

            for provider in providers:
                console.print(f"[yellow]Opening {provider.value}...[/yellow]")
                page = await session.get_provider_page(provider)
                await session.wait_for_manual_login(page, provider)
                console.print(f"[green]Authenticated to {provider.value}![/green]")

    try:
        asyncio.run(_run())
    except Exception as exc:
        _error_exit(f"Browser auth failed: {exc}")


@auth_app.command("drive")
def auth_drive() -> None:
    """Run Google Drive OAuth flow (Desktop App)."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()
    console.print("[cyan]Starting Google Drive OAuth flow...[/cyan]")
    try:
        from .drive.oauth import get_credentials
        creds = get_credentials(
            Path(settings.google_drive_credentials_json),
            Path(settings.google_drive_token_json),
        )
        console.print("[green]Google Drive OAuth successful! Token saved.[/green]")
    except FileNotFoundError as exc:
        _error_exit(str(exc))
    except Exception as exc:
        _error_exit(f"Drive auth failed: {exc}")


# ---------------------------------------------------------------------------
# crawl
# ---------------------------------------------------------------------------

@app.command()
def crawl(
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", "-p", help="chatgpt | gemini | all"),
    ] = "all",
    limit: Annotated[
        Optional[int],
        typer.Option("--limit", "-n", help="Max conversations per provider"),
    ] = None,
    full: Annotated[
        bool,
        typer.Option("--full", help="Non-incremental: re-extract all conversations"),
    ] = False,
    backfill: Annotated[
        bool,
        typer.Option(
            "--backfill",
            help=(
                "ChatGPT only: run Phase 1 sidebar harvest (slow-scroll to load all "
                "conversations) before Phase 2 extraction. "
                "DO NOT interact with the browser during harvest."
            ),
        ),
    ] = False,
) -> None:
    """Crawl ChatGPT and/or Gemini conversations."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .models import Provider

    providers = _resolve_providers(provider, settings)
    incremental = not full

    if backfill:
        console.print(
            Panel(
                "[bold yellow]⚠  BACKFILL MODE ACTIVE ⚠[/bold yellow]\n\n"
                "Phase 1 will slowly scroll the ChatGPT sidebar to force-load all older\n"
                "conversations.  This will run for at least "
                f"[bold]{settings.chatgpt_backfill_min_minutes}[/bold] minutes "
                f"(max {settings.chatgpt_backfill_max_minutes} min).\n\n"
                "[bold red]DO NOT click, type, or interact with the ChatGPT browser "
                "window during harvesting.[/bold red]\n\n"
                "Phase 2 extraction will start automatically once Phase 1 completes.\n"
                "Progress is saved to: data/state/chatgpt_backfill_index.json",
                title="ChatGPT Backfill",
                border_style="yellow",
            )
        )

    async def _run() -> None:
        from .pipeline.crawl import CrawlPipeline
        from .reports.summary import print_run_summary

        db = _get_db(settings)
        pipeline = CrawlPipeline(settings=settings, db_conn=db)
        run = await pipeline.run(
            providers=providers,
            limit=limit,
            incremental=incremental,
            backfill=backfill,
        )
        print_run_summary(run)
        if not run.success:
            raise typer.Exit(code=1)

    try:
        asyncio.run(_run())
    except typer.Exit:
        raise
    except Exception as exc:
        _error_exit(f"Crawl failed: {exc}")


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

@app.command()
def normalize(
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", "-p", help="chatgpt | gemini | all"),
    ] = "all",
) -> None:
    """Normalize raw conversations to clean JSON + Markdown."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .pipeline.normalize import normalize_all

    db = _get_db(settings)
    prov_filter = None if provider == "all" else provider
    count = normalize_all(db, normalized_dir=settings.normalized_dir, provider=prov_filter)
    console.print(f"[green]Normalized {count} conversations.[/green]")


# ---------------------------------------------------------------------------
# cluster
# ---------------------------------------------------------------------------

@app.command()
def cluster() -> None:
    """Run embedding-based topic clustering on all conversations."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .pipeline.cluster import ClusterPipeline
    from .reports.summary import print_topic_summary

    db = _get_db(settings)
    pipeline = ClusterPipeline(settings=settings)
    try:
        topics = pipeline.run(db)
        print_topic_summary(topics)
        console.print(f"[green]Clustered into {len(topics)} topics.[/green]")
    except Exception as exc:
        _error_exit(f"Clustering failed: {exc}")


# ---------------------------------------------------------------------------
# curate
# ---------------------------------------------------------------------------

@app.command()
def curate() -> None:
    """Generate curated canonical documents for each topic cluster."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .pipeline.curate import CurationPipeline

    db = _get_db(settings)
    pipeline = CurationPipeline(settings=settings)
    try:
        docs = pipeline.run(db)
        console.print(f"[green]Generated {len(docs)} canonical topic documents.[/green]")
    except Exception as exc:
        _error_exit(f"Curation failed: {exc}")


# ---------------------------------------------------------------------------
# drive sync
# ---------------------------------------------------------------------------

@drive_app.command("sync")
def drive_sync() -> None:
    """Sync local artifacts to Google Drive."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    if not settings.drive_enabled:
        console.print("[yellow]Drive sync is disabled (drive_enabled=false). Enable in config.[/yellow]")
        raise typer.Exit(code=0)

    try:
        from .drive.oauth import get_credentials
        from .drive.api import DriveAPI
        from .pipeline.drive_sync import DriveSyncPipeline
        from .reports.summary import print_drive_summary

        creds = get_credentials(
            Path(settings.google_drive_credentials_json),
            Path(settings.google_drive_token_json),
        )
        drive_api = DriveAPI(creds)
        db = _get_db(settings)
        pipeline = DriveSyncPipeline(settings=settings, db_conn=db, drive_api=drive_api)
        stats = pipeline.run()
        print_drive_summary(stats)
    except Exception as exc:
        _error_exit(f"Drive sync failed: {exc}")


# ---------------------------------------------------------------------------
# sync all
# ---------------------------------------------------------------------------

@sync_app.command("all")
def sync_all(
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", "-p", help="chatgpt | gemini | all"),
    ] = "all",
    limit: Annotated[
        Optional[int],
        typer.Option("--limit", "-n"),
    ] = None,
) -> None:
    """Run full pipeline: crawl -> normalize -> cluster -> curate -> drive sync."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    providers = _resolve_providers(provider, settings)

    async def _run() -> None:
        from .pipeline.crawl import CrawlPipeline
        from .pipeline.normalize import normalize_all
        from .pipeline.cluster import ClusterPipeline
        from .pipeline.curate import CurationPipeline
        from .reports.summary import print_run_summary, print_topic_summary

        db = _get_db(settings)

        console.print("[bold cyan]Step 1/5: Crawl[/bold cyan]")
        crawl_pipeline = CrawlPipeline(settings=settings, db_conn=db)
        run = await crawl_pipeline.run(providers=providers, limit=limit, incremental=True)
        print_run_summary(run)

        console.print("[bold cyan]Step 2/5: Normalize[/bold cyan]")
        prov_filter = providers[0].value if len(providers) == 1 else None
        normalize_all(db, normalized_dir=settings.normalized_dir, provider=prov_filter)

        console.print("[bold cyan]Step 3/5: Cluster[/bold cyan]")
        cluster_pipeline = ClusterPipeline(settings=settings)
        topics = cluster_pipeline.run(db)
        print_topic_summary(topics)

        console.print("[bold cyan]Step 4/5: Curate[/bold cyan]")
        curate_pipeline = CurationPipeline(settings=settings)
        docs = curate_pipeline.run(db)
        console.print(f"[green]{len(docs)} documents curated.[/green]")

        console.print("[bold cyan]Step 5/5: Drive Sync[/bold cyan]")
        if settings.drive_enabled:
            from .drive.oauth import get_credentials
            from .drive.api import DriveAPI
            from .pipeline.drive_sync import DriveSyncPipeline
            from .reports.summary import print_drive_summary
            creds = get_credentials(
                Path(settings.google_drive_credentials_json),
                Path(settings.google_drive_token_json),
            )
            drive_api = DriveAPI(creds)
            sync_pipeline = DriveSyncPipeline(settings=settings, db_conn=db, drive_api=drive_api)
            stats = sync_pipeline.run()
            print_drive_summary(stats)
        else:
            console.print("[dim]Drive sync skipped (not enabled).[/dim]")

    try:
        asyncio.run(_run())
    except Exception as exc:
        _error_exit(f"Sync failed: {exc}")


# ---------------------------------------------------------------------------
# reindex
# ---------------------------------------------------------------------------

@app.command()
def reindex(
    full: Annotated[
        bool,
        typer.Option("--full", help="Re-run normalize+cluster+curate from scratch"),
    ] = False,
) -> None:
    """Re-run normalize + cluster + curate pipelines."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .pipeline.normalize import normalize_all
    from .pipeline.cluster import ClusterPipeline
    from .pipeline.curate import CurationPipeline
    from .reports.summary import print_topic_summary

    db = _get_db(settings)

    console.print("[cyan]Normalizing...[/cyan]")
    normalize_all(db, normalized_dir=settings.normalized_dir)

    console.print("[cyan]Clustering...[/cyan]")
    cluster_pipeline = ClusterPipeline(settings=settings)
    try:
        topics = cluster_pipeline.run(db)
        print_topic_summary(topics)
    except Exception as exc:
        console.print(f"[yellow]Clustering failed (skipping): {exc}[/yellow]")
        topics = []

    console.print("[cyan]Curating...[/cyan]")
    curate_pipeline = CurationPipeline(settings=settings)
    docs = curate_pipeline.run(db)
    console.print(f"[green]Reindex complete. {len(topics)} topics, {len(docs)} docs.[/green]")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@app.command()
def report() -> None:
    """Print summary of database state."""
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .db import list_conversations, list_topics, list_canonical_docs
    from .reports.summary import print_topic_summary
    from rich.table import Table

    db = _get_db(settings)
    convs = list_conversations(db)
    topics = list_topics(db)
    docs = list_canonical_docs(db)

    summary_table = Table(title="AI Archive — Database State", header_style="bold cyan")
    summary_table.add_column("Item", style="bold")
    summary_table.add_column("Count", justify="right")

    summary_table.add_row("Conversations", str(len(convs)))

    from collections import Counter
    by_provider = Counter(c.provider.value for c in convs)
    for prov, cnt in by_provider.items():
        summary_table.add_row(f"  {prov}", str(cnt))

    summary_table.add_row("Topic Clusters", str(len(topics)))
    summary_table.add_row("Canonical Docs", str(len(docs)))

    console.print(summary_table)

    if topics:
        print_topic_summary(topics[:20])


# ---------------------------------------------------------------------------
# import gemini-downloads
# ---------------------------------------------------------------------------

@import_app.command("gemini-downloads")
def import_gemini_downloads(
    path: Annotated[
        Optional[Path],
        typer.Argument(
            help=(
                "Folder (or single file) containing saved Gemini HTML/JSON files. "
                "Defaults to ~/Downloads."
            ),
        ),
    ] = None,
) -> None:
    """Import manually-saved Gemini conversations from a folder or file.

    Supports HTML pages saved from the Gemini web app and JSON exports
    (e.g. Google Takeout). Imported conversations are immediately visible
    to normalize / cluster / curate.

    Examples
    --------
      uv run ai-archive import gemini-downloads
      uv run ai-archive import gemini-downloads ~/Downloads
      uv run ai-archive import gemini-downloads ~/Downloads/my-chat.html
    """
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    source = Path(path).expanduser() if path else Path.home() / "Downloads"

    if not source.exists():
        _error_exit(f"Path does not exist: {source}")

    console.print(f"[cyan]Importing Gemini conversations from:[/cyan] {source}")

    from .importers.gemini_html import GeminiDownloadImporter
    from rich.table import Table

    db = _get_db(settings)
    importer = GeminiDownloadImporter(settings=settings, db_conn=db)

    try:
        stats = importer.import_path(source)
    except FileNotFoundError as exc:
        _error_exit(str(exc))
        return

    table = Table(title="Gemini Import Results", header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Imported", f"[green]{stats.imported}[/green]")
    table.add_row("Skipped (unchanged)", str(stats.skipped))
    table.add_row("Errors", f"[red]{stats.errors}[/red]" if stats.errors else "0")
    console.print(table)

    if stats.error_files:
        console.print("[yellow]Files with errors:[/yellow]")
        for ef in stats.error_files:
            console.print(f"  [dim]{ef}[/dim]")

    if stats.imported == 0 and stats.errors == 0:
        console.print(
            "[yellow]No new conversations found. "
            "Check that the folder contains saved Gemini HTML or JSON files.[/yellow]"
        )
        return

    if stats.imported > 0:
        console.print(
            f"\n[green]Done! {stats.imported} conversation(s) added to the archive.[/green]\n"
            "[dim]Run the following to process them:[/dim]\n"
            "  uv run ai-archive normalize\n"
            "  uv run ai-archive cluster\n"
            "  uv run ai-archive curate\n"
            "  uv run ai-archive report"
        )


# ---------------------------------------------------------------------------
# import scan-gemini
# ---------------------------------------------------------------------------

@import_app.command("scan-gemini")
def import_scan_gemini(
    path: Annotated[
        Optional[Path],
        typer.Argument(help="Caminho extra a escanear (além dos padrões)."),
    ] = None,
    do_import: bool = typer.Option(False, "--import", help="Importar automaticamente os arquivos novos encontrados."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostrar o que seria importado sem importar."),
) -> None:
    """Escaneia caminhos padrão (Downloads, Windows /mnt/c/) em busca de conversas Gemini não importadas.

    Por default apenas lista os arquivos novos. Use --import para importar.

    Exemplos
    --------
      uv run ai-archive import scan-gemini
      uv run ai-archive import scan-gemini --import
      uv run ai-archive import scan-gemini /mnt/c/Users/pedro/Downloads/AI-Archives --import
    """
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .importers.gemini_scanner import GeminiScanner
    from rich.table import Table

    db = _get_db(settings)
    scanner = GeminiScanner(db_conn=db, settings=settings)

    extra = [Path(path)] if path else None
    console.print("[cyan]Escaneando caminhos em busca de conversas Gemini não importadas...[/cyan]")
    result = scanner.scan(extra_paths=extra)

    table = Table(title="Gemini Scan", header_style="bold cyan")
    table.add_column("Métrica", style="bold")
    table.add_column("Qtd", justify="right")
    table.add_row("Arquivos encontrados", str(result.total_found))
    table.add_row("Já importados", f"[dim]{result.already_imported}[/dim]")
    table.add_row("NOVOS (não importados)", f"[bold green]{result.new_count}[/bold green]")
    console.print(table)

    if result.new_files:
        console.print("\n[yellow]Arquivos novos encontrados:[/yellow]")
        for p in result.new_files[:30]:
            console.print(f"  [green]+[/green] {p.name}  [dim]{p.parent}[/dim]")
        if result.new_count > 30:
            console.print(f"  [dim]... e mais {result.new_count - 30} arquivos[/dim]")

        if do_import or dry_run:
            console.print(f"\n{'[yellow][dry-run]' if dry_run else '[cyan]'} Importando {result.new_count} arquivos...")
            imported, errors = scanner.import_new(result, dry_run=dry_run)
            if not dry_run:
                console.print(f"[green]Importados: {imported}  Erros: {errors}[/green]")
                if imported > 0:
                    console.print(
                        "\n[dim]Processar com:[/dim]\n"
                        "  uv run ai-archive normalize\n"
                        "  uv run ai-archive cluster\n"
                        "  uv run ai-archive curate\n"
                        "  uv run ai-archive report"
                    )
        else:
            console.print(
                f"\n[dim]Para importar: [/dim][bold]uv run ai-archive import scan-gemini --import[/bold]"
            )
    else:
        console.print("[green]Tudo já importado! Nenhum arquivo novo encontrado.[/green]")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@app.command()
def export(
    output: Annotated[
        Optional[Path],
        typer.Option(
            "--output", "-o",
            help=(
                "Destination folder for the HTML export. "
                "Defaults to ~/Downloads/ai-archive (Windows Downloads via WSL)."
            ),
        ),
    ] = None,
) -> None:
    """Export curated topics to styled HTML files in Windows Downloads\\ai-archive\\.

    Creates one HTML page per topic plus an index.html with search,
    and a COMO-USAR.txt guide.  Existing files are always overwritten
    with the latest version.

    Examples
    --------
      uv run ai-archive export
      uv run ai-archive export --output /mnt/c/Users/pedro/Desktop/ai-archive
    """
    settings = _get_settings()
    _init_logging(settings)
    _print_run_id()

    from .pipeline.export import ExportPipeline
    from rich.table import Table

    # Resolve output path
    out_dir: Path | None = None
    if output:
        out_dir = Path(output).expanduser()
    else:
        # Auto-detect Windows Downloads via WSL
        wsl_downloads = Path("/mnt/c/Users/pedro/Downloads/ai-archive")
        if wsl_downloads.parent.parent.exists():
            out_dir = wsl_downloads
        else:
            out_dir = Path.home() / "Downloads" / "ai-archive"

    console.print(f"[cyan]Exporting curated topics to:[/cyan] {out_dir}")

    pipeline = ExportPipeline(settings=settings, output_dir=out_dir)
    stats = pipeline.run()

    table = Table(title="Export Results", header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Topics exported", f"[green]{stats['exported']}[/green]")
    table.add_row("Skipped (no .md)", str(stats["skipped"]))
    table.add_row(
        "Errors",
        f"[red]{stats['errors']}[/red]" if stats["errors"] else "0",
    )
    console.print(table)

    if stats["exported"] > 0:
        index_path = out_dir / "index.html"
        console.print(
            f"\n[green]Done![/green] Open this file in your browser:\n"
            f"  [bold]{index_path}[/bold]\n"
            f"\n[dim]Files are always overwritten — re-run after each crawl+curate cycle.[/dim]"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_providers(provider_arg: str | None, settings=None) -> list:
    from .models import Provider

    if not provider_arg or provider_arg.lower() == "all":
        providers = []
        if settings is None or settings.chatgpt_enabled:
            providers.append(Provider.CHATGPT)
        if settings is None or settings.gemini_enabled:
            providers.append(Provider.GEMINI)
        if settings is not None and not providers:
            _error_exit("No providers enabled. Set CHATGPT_ENABLED=true or GEMINI_ENABLED=true in .env.")
        return providers
    mapping = {
        "chatgpt": Provider.CHATGPT,
        "gemini": Provider.GEMINI,
    }
    p = mapping.get(provider_arg.lower())
    if p is None:
        _error_exit(f"Unknown provider: {provider_arg}. Use chatgpt, gemini, or all.")
    return [p]


if __name__ == "__main__":
    app()
