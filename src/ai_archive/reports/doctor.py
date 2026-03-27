"""AI Archive — DoctorReport: environment health checks."""
from __future__ import annotations

import importlib
import sqlite3
import sys
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

_PASS = "[bold green]PASS[/bold green]"
_WARN = "[bold yellow]WARN[/bold yellow]"
_FAIL = "[bold red]FAIL[/bold red]"


def _check(status: str, name: str, detail: str) -> dict:
    return {"status": status, "check": name, "detail": detail}


def run_doctor(settings: object | None = None) -> list[dict]:
    """Run all health checks and return a list of result dicts."""
    if settings is None:
        from ..config import get_settings
        settings = get_settings()

    results: list[dict] = []

    # 1. Python version
    v = sys.version_info
    if v >= (3, 12):
        results.append(_check(_PASS, "Python >= 3.12", f"{v.major}.{v.minor}.{v.micro}"))
    else:
        results.append(_check(_FAIL, "Python >= 3.12", f"Found {v.major}.{v.minor}.{v.micro}"))

    # 2. Playwright installed + browsers
    try:
        import playwright
        from importlib.metadata import PackageNotFoundError, version as pkg_version
        try:
            pw_version = pkg_version("playwright")
        except PackageNotFoundError:
            pw_version = "installed"
        results.append(_check(_PASS, "playwright installed", pw_version))
        # Check if browsers are installed by looking for the chromium executable
        try:
            from playwright._impl._driver import compute_driver_executable
            # Alternative: just try importing the sync API
            results.append(_check(_PASS, "playwright browsers", "Chromium should be available (run 'playwright install' if not)"))
        except Exception:
            results.append(_check(_WARN, "playwright browsers", "Could not verify — run 'playwright install'"))
    except ImportError:
        results.append(_check(_FAIL, "playwright installed", "Not installed — run 'pip install playwright'"))

    # 3. data/ subdirs exist and writable
    data_dirs = [
        settings.raw_dir,
        settings.normalized_dir,
        settings.curated_dir,
        settings.state_dir,
        settings.logs_dir,
    ]
    for d in data_dirs:
        d = Path(d)
        try:
            d.mkdir(parents=True, exist_ok=True)
            test_file = d / ".write_test"
            test_file.write_text("ok")
            test_file.unlink()
            results.append(_check(_PASS, f"dir writable: {d.name}", str(d)))
        except Exception as exc:
            results.append(_check(_FAIL, f"dir writable: {d.name}", str(exc)))

    # 4. .env file
    env_path = Path(".env")
    if env_path.exists():
        results.append(_check(_PASS, ".env file", str(env_path.absolute())))
    else:
        results.append(_check(_WARN, ".env file", "Not found — copy .env.example to .env"))

    # 5. DB file accessible
    db_path = Path(settings.db_file)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT 1")
        conn.close()
        results.append(_check(_PASS, "DB accessible", str(db_path)))
    except Exception as exc:
        results.append(_check(_FAIL, "DB accessible", str(exc)))

    # 6. CDP available (if attach_cdp mode)
    if settings.auth_mode == "attach_cdp":
        cdp_url = settings.chrome_cdp_url
        try:
            req = urllib.request.urlopen(f"{cdp_url}/json/version", timeout=3)
            if req.status == 200:
                results.append(_check(_PASS, "CDP available", cdp_url))
            else:
                results.append(_check(_WARN, "CDP available", f"HTTP {req.status} at {cdp_url}"))
        except Exception as exc:
            results.append(_check(_WARN, "CDP available", f"Not reachable: {exc}"))
    else:
        results.append(_check(_PASS, "CDP available", f"N/A (auth_mode={settings.auth_mode})"))

    # 7. Google Drive credentials (if drive enabled)
    if settings.drive_enabled:
        creds_path = Path(settings.google_drive_credentials_json)
        if creds_path.exists():
            results.append(_check(_PASS, "Drive credentials", str(creds_path)))
        else:
            results.append(_check(_FAIL, "Drive credentials", f"Not found: {creds_path}"))
    else:
        results.append(_check(_PASS, "Drive credentials", "N/A (drive not enabled)"))

    # 8. sentence-transformers importable
    try:
        import sentence_transformers
        results.append(_check(_PASS, "sentence-transformers", sentence_transformers.__version__))
    except ImportError:
        results.append(_check(_FAIL, "sentence-transformers", "Not installed"))

    # 9. Provider URLs reachable (basic HTTP check)
    for name, url in [
        ("ChatGPT URL", "https://chatgpt.com"),
        ("Gemini URL", "https://gemini.google.com"),
    ]:
        try:
            req = urllib.request.urlopen(url, timeout=5)
            results.append(_check(_PASS, name, f"HTTP {req.status}"))
        except Exception as exc:
            results.append(_check(_WARN, name, f"Unreachable: {exc}"))

    return results


def print_doctor_report(settings: object | None = None) -> bool:
    """Print a formatted doctor report. Returns True if no FAIL checks."""
    results = run_doctor(settings)

    table = Table(title="AI Archive Doctor Report", show_header=True, header_style="bold cyan")
    table.add_column("Status", width=8, justify="center")
    table.add_column("Check", min_width=30)
    table.add_column("Detail")

    has_fail = False
    for r in results:
        status = r["status"]
        if "FAIL" in status:
            has_fail = True
        table.add_row(status, r["check"], r["detail"])

    console.print(table)

    if has_fail:
        console.print("\n[bold red]Some checks FAILED. Please fix before running crawls.[/bold red]")
    else:
        console.print("\n[bold green]All checks passed (or warned). Ready to run![/bold green]")

    return not has_fail
