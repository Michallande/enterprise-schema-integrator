"""Enterprise Schema Integrator: executive CLI.

Usage:
  python main.py [--data-file PATH] [--mock]

Without --data-file, prompts for a path (drag-and-drop a file into the
terminal works). Uses Anthropic when ANTHROPIC_API_KEY is set, else OpenAI
when OPENAI_API_KEY is set, else an offline mock LLM so the demo always works.
"""

import argparse
import json
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import Any, List, Optional

from dotenv import load_dotenv

load_dotenv()

PLACEHOLDER_PREFIX = "your_"
DEFAULT_DATA_FILE = "messy_customer_data.json"


def _has_real_key(name: str) -> bool:
    value = os.getenv(name, "").strip()
    return bool(value) and not value.startswith(PLACEHOLDER_PREFIX)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enterprise Schema Integrator & Data Readiness Agent")
    parser.add_argument("--data-file",
                        help=f"Path to source customer JSON (prompts if omitted; default: {DEFAULT_DATA_FILE})")
    parser.add_argument("--mock", action="store_true",
                        help="Force offline mock LLM even if an API key is set")
    return parser.parse_args()


args = parse_args()
mock_mode = args.mock or not (_has_real_key("ANTHROPIC_API_KEY") or _has_real_key("OPENAI_API_KEY"))
tracing = not mock_mode and _has_real_key("LANGCHAIN_API_KEY")
# langsmith caches env lookups, so tracing must be decided before it is imported.
os.environ["LANGCHAIN_TRACING_V2"] = "true" if tracing else "false"

from rich import box  # noqa: E402
from rich.align import Align  # noqa: E402
from rich.console import Console, Group  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402
from rich.text import Text  # noqa: E402
from rich.theme import Theme  # noqa: E402

import agent  # noqa: E402
from schemas import ReadinessReport  # noqa: E402

# Blue palette; red/yellow are reserved for severity and risk signals.
# Text uses the terminal's own foreground color: maximum contrast on both dark
# and light backgrounds (a fixed black or grey vanishes on one of them).
THEME = Theme({
    "accent": "bold deep_sky_blue1",
    "primary": "dodger_blue1",
    "border": "dodger_blue2",
    "heading": "bold default",
    "body": "default",
    "secondary": "default",
    "critical": "bold red",
    "warning": "bold yellow",
    "info": "bold cyan",
})
console = Console(theme=THEME, log_path=False)
# The CLI reports failures itself; hide instructor's raw retry logs.
logging.getLogger("instructor").setLevel(logging.CRITICAL)
# Using the key from .env is intentional; hide the SDK's credential-precedence notice.
logging.getLogger("anthropic.lib.credentials").setLevel(logging.ERROR)

SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
SEVERITY_LEGEND = [
    ("CRITICAL", "Blockers that prevent record loading (missing required fields, invalid formats)."),
    ("WARNING", "Data quality risks that need review (ambiguous date formats)."),
    ("INFO", "Non-breaking formatting issues or minor cleanups."),
]


def score_style(score: int) -> tuple:
    if score >= 85:
        return "accent", "READY TO LOAD"
    if score >= 60:
        return "warning", "NEEDS REMEDIATION"
    return "critical", "NOT READY"


def confidence_style(confidence: float) -> str:
    # Below the mapping threshold, keep the risk color so weak mappings stand out.
    return "bold cyan" if confidence >= agent.MIN_MAPPING_CONFIDENCE else "warning"


def section(number: int, title: str) -> None:
    console.print()
    console.rule(Text.assemble((f" {number} ", "accent"), (f"{title} ", "heading")),
                 style="border", align="left")
    console.print()


def themed_table(**kwargs) -> Table:
    options = dict(box=box.ROUNDED, border_style="border", header_style="accent", expand=True, padding=(0, 1))
    return Table(**{**options, **kwargs})


def render_header() -> None:
    title = Text("ENTERPRISE SCHEMA INTEGRATOR & DATA READINESS AGENT", style="heading")
    mode = (Text(" MOCK MODE ", style="bold black on yellow") + Text("  offline LLM stand-in, no API calls", style="secondary")
            if mock_mode else
            Text(" LIVE MODE ", style="bold bright_white on dodger_blue2")
            + Text(f"  {agent.PROVIDER} · {agent.MODEL}", style="secondary"))
    trace = Text(f"LangSmith tracing: {'on' if tracing else 'off'}", style="secondary")
    console.print(Panel(Group(Align.center(title), Text(), Align.center(mode), Align.center(trace)),
                        box=box.ROUNDED, border_style="border", padding=(1, 2)))


def render_mappings(report: ReadinessReport) -> None:
    section(1, "Schema Mapping")
    # Sized to its content rather than the terminal width.
    table = themed_table(expand=False)
    table.add_column("Source Field", style="cyan", no_wrap=True)
    table.add_column("Target Field", style="bold bright_blue", no_wrap=True)
    table.add_column("Confidence", justify="right", no_wrap=True)
    for m in report.mapped_fields:
        table.add_row(m.source_field_name, m.target_field_name,
                      Text(f"{m.confidence_score:.0%}", style=confidence_style(m.confidence_score)))
    console.print(table)


def render_issues(report: ReadinessReport) -> None:
    section(2, "Data Quality")
    if not report.data_issues:
        console.print(Panel(Text("No data issues detected.", style="accent"),
                            box=box.ROUNDED, border_style="border"))
        return
    table = themed_table()
    table.add_column("Severity", no_wrap=True)
    table.add_column("Row", style="body", justify="right", no_wrap=True)
    table.add_column("Field", style="primary", no_wrap=True)
    table.add_column("Issue", style="body", no_wrap=True)
    table.add_column("Description", style="secondary", ratio=1)
    issues = sorted(report.data_issues,
                    key=lambda i: (SEVERITY_ORDER[i.severity], i.row_index if i.row_index is not None else -1))
    for i in issues:
        table.add_row(Text(i.severity, style=i.severity.lower()),
                      "all" if i.row_index is None else str(i.row_index),
                      i.field_name, i.issue_type, Text(i.description))
    console.print(table)

    legend = Table.grid(padding=(0, 2))
    legend.add_column(no_wrap=True)
    legend.add_column(style="secondary")
    for severity, meaning in SEVERITY_LEGEND:
        legend.add_row(Text(f"● {severity}", style=severity.lower()), meaning)
    console.print(Panel(legend, title="[secondary]Severity legend[/]", title_align="left",
                        box=box.ROUNDED, border_style="border", padding=(0, 1)))


def render_score(report: ReadinessReport) -> None:
    section(3, "Readiness Assessment")
    style, verdict = score_style(report.data_health_score)
    counts = {s: sum(1 for i in report.data_issues if i.severity == s) for s in SEVERITY_ORDER}
    body = Group(
        Align.center(Text.assemble((f"{report.data_health_score}", style), ("/100", "secondary"))),
        Align.center(Text(verdict, style=style)),
        Text(),
        Align.center(Text.assemble(
            (f"{report.total_records_processed} records", "body"), ("   ·   ", "secondary"),
            (f"{counts['CRITICAL']} critical", "critical"), ("   ·   ", "secondary"),
            (f"{counts['WARNING']} warning", "warning"), ("   ·   ", "secondary"),
            (f"{counts['INFO']} info", "info"),
        )),
    )
    console.print(Panel(body, title="[heading]Data Health Score[/]", box=box.ROUNDED,
                        border_style="border", padding=(1, 2)))


def render_summary(report: ReadinessReport) -> None:
    console.print(Panel(Text(report.executive_summary, style="body"), title="[heading]Executive Summary[/]",
                        title_align="left", box=box.ROUNDED, border_style="border", padding=(1, 2)))
    actions = Table.grid(padding=(0, 1))
    actions.add_column(style="accent", no_wrap=True)
    actions.add_column(style="body")
    for n, item in enumerate(report.key_action_items):
        if n:
            actions.add_row()  # Blank line between items.
        actions.add_row("▸", Text(item))
    console.print(Panel(actions, title="[heading]Key Action Items[/]", title_align="left",
                        box=box.ROUNDED, border_style="border", padding=(1, 2)))


def clean_path(raw: str) -> Path:
    """Turn typed or drag-and-dropped input into a path.

    Terminals paste dropped files quoted ('/a b.json') or backslash-escaped
    (/a\\ b.json), often with a trailing space; shlex undoes both.
    """
    raw = raw.strip()
    if os.name == "nt":  # Windows paths use backslashes; only strip quotes.
        return Path(raw.strip("'\"")).expanduser()
    try:
        parts = shlex.split(raw)
        if len(parts) == 1:
            raw = parts[0]
    except ValueError:  # Unbalanced quotes: use the input as typed.
        pass
    return Path(raw).expanduser()


def validate_data_file(path: Path) -> Optional[str]:
    """Return an error message, or None if the file is a usable JSON record list."""
    if not path.is_file():
        return f"File not found: {path}"
    try:
        records: Any = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return f"{path.name} is not valid JSON: {exc}"
    if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
        return f"{path.name} must contain a non-empty JSON array of records."
    return None


def choose_data_file() -> Optional[Path]:
    if args.data_file:
        path = clean_path(args.data_file)
        error = validate_data_file(path)
        if error:
            console.print(f"[bold red]Error:[/] {error}")
            return None
        return path

    while True:
        try:
            raw = console.input(
                f"[accent]📁 Enter data file path (or press Enter for '{DEFAULT_DATA_FILE}'):[/] ")
        except EOFError:  # Non-interactive stdin: fall back to the default.
            raw = ""
            console.print()
        path = clean_path(raw) if raw.strip() else Path(DEFAULT_DATA_FILE)
        error = validate_data_file(path)
        if not error:
            records: List[Any] = json.loads(path.read_text())
            console.print(f"[accent]✓[/] [body]Loaded[/] [heading]{path.name}[/] [secondary]({len(records)} records)[/]\n")
            return path
        console.print(f"[critical]✗[/] [body]{error}[/]")
        if not sys.stdin.isatty():
            return None


def main() -> int:
    render_header()

    try:
        data_file = choose_data_file()
    except KeyboardInterrupt:
        console.print("\n[dim]Cancelled.[/]")
        return 130
    if data_file is None:
        return 1

    if mock_mode:
        from mock_llm import build_mock_client
        client = build_mock_client()
    else:
        client = agent.build_client()

    try:
        with console.status("[accent]Starting agent...", spinner="dots") as status:
            current = []

            def on_stage(stage: str) -> None:
                if current:
                    console.log(f"[accent]✓[/] [body]{current[-1]}[/]")
                current.append(stage)
                status.update(f"[accent]{stage}...")

            report = agent.run_pipeline(str(data_file), client=client, on_stage=on_stage)
            console.log(f"[accent]✓[/] [body]{current[-1]}[/]")
    except KeyboardInterrupt:
        console.print("\n[secondary]Cancelled.[/]")
        return 130
    except Exception as exc:  # Surface API/auth errors cleanly in a demo.
        console.print(f"[bold red]Agent failed:[/] {type(exc).__name__}: {exc}")
        return 1

    render_mappings(report)
    render_issues(report)
    render_score(report)
    render_summary(report)
    console.print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
