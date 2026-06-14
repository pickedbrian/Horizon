"""Dry-run Sinomis AI publishing from Horizon-generated Markdown."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from ..models import SinomisConfig
from ..storage.manager import ConfigError, StorageManager
from .sinomis import SinomisPublisher, build_sinomis_import_payload

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview Horizon -> Sinomis AI import and DingTalk notification."
    )
    parser.add_argument(
        "markdown",
        nargs="?",
        help="Markdown summary path. Defaults to the newest data/summaries/*-zh.md file.",
    )
    parser.add_argument("--date", help="Issue date, defaults to the date inferred from the filename/header.")
    parser.add_argument("--issue-type", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--language", default="zh")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Only print the payload that Horizon would send; do not call sinomis-ai.",
    )
    args = parser.parse_args()

    load_dotenv()

    try:
        storage = StorageManager(data_dir="data")
        config = storage.load_config()
    except (FileNotFoundError, ConfigError) as exc:
        console.print(f"[bold red]❌ Failed to load Horizon config: {exc}[/bold red]")
        sys.exit(1)

    markdown_path = resolve_markdown_path(args.markdown)
    markdown = markdown_path.read_text(encoding="utf-8")
    date = args.date or infer_date(markdown_path, markdown)

    sinomis_config = config.sinomis or SinomisConfig(enabled=True)
    payload = build_sinomis_import_payload(
        date=date,
        markdown=markdown,
        issue_type=args.issue_type,
        dry_run=True,
    )

    console.print(
        Panel.fit(
            f"[bold]file[/bold]: {markdown_path}\n"
            f"[bold]date[/bold]: {date}\n"
            f"[bold]issueType[/bold]: {args.issue_type}\n"
            f"[bold]target[/bold]: {sinomis_config.base_url}{sinomis_config.import_path}",
            title="Horizon -> Sinomis Dry Run",
        )
    )
    print_json("Payload Sent To Sinomis", payload)

    if args.local_only:
        console.print("[yellow]local-only enabled; skipped sinomis-ai request.[/yellow]")
        return

    publisher = SinomisPublisher(sinomis_config, console=console)
    try:
        result = asyncio.run(
            publisher.preview_daily_summary(
                date=date,
                markdown=markdown,
                language=args.language,
                issue_type=args.issue_type,
            )
        )
    except Exception as exc:
        console.print(f"[bold red]❌ Sinomis dry-run request failed: {exc}[/bold red]")
        sys.exit(1)

    if result is None:
        sys.exit(1)

    print_json("Sinomis Dry-Run Response", result)

    issue = result.get("issue") if isinstance(result, dict) else None
    dingtalk = result.get("dingtalk") if isinstance(result, dict) else None

    if isinstance(dingtalk, dict):
        console.print(Panel(str(dingtalk.get("title", "")), title="DingTalk Title"))
        console.print(
            Panel(
                str(dingtalk.get("text", "")),
                title="DingTalk Markdown Preview",
            )
        )

    if isinstance(issue, dict):
        console.print(
            Panel(
                str(issue.get("contentMarkdown", "")),
                title="Full Daily Report Markdown",
            )
        )


def resolve_markdown_path(raw_path: str | None) -> Path:
    if raw_path:
        path = Path(raw_path)
    else:
        candidates = sorted(Path("data/summaries").glob("*-zh.md"))
        if not candidates:
            raise SystemExit("No zh summary found under data/summaries.")
        path = candidates[-1]

    if not path.exists():
        raise SystemExit(f"Markdown file not found: {path}")
    return path


def infer_date(path: Path, markdown: str) -> str:
    for token in path.stem.split("-"):
        if len(token) == 4 and token.isdigit():
            break
    import re

    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", f"{path.name}\n{markdown}")
    if not match:
        raise SystemExit("Could not infer date. Pass --date YYYY-MM-DD.")
    return match.group(1)


def print_json(title: str, value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    console.print(Panel(Syntax(text, "json", word_wrap=True), title=title))


if __name__ == "__main__":
    main()
