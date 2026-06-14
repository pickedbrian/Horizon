"""Publish Horizon summaries to Sinomis AI."""

import os
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from rich.console import Console

from ..models import SinomisConfig


class SinomisPublisher:
    """Push generated Markdown reports into Sinomis AI's news module."""

    def __init__(
        self,
        config: SinomisConfig,
        console: Optional[Console] = None,
    ) -> None:
        self.config = config
        self.console = console or Console()

    async def publish_daily_summary(
        self,
        *,
        date: str,
        markdown: str,
        language: str,
        issue_type: str = "daily",
    ) -> None:
        if not self.config.enabled:
            return
        if language != self.config.language:
            return

        base_url = (self.config.base_url or "").strip().rstrip("/")
        if not base_url or "${" in base_url:
            self.console.print(
                "[yellow]⚠️  Sinomis publish skipped: base_url is not configured[/yellow]\n"
            )
            return

        api_key = os.environ.get(self.config.management_api_key_env, "").strip()
        if not api_key:
            self.console.print(
                f"[yellow]⚠️  Sinomis publish skipped: {self.config.management_api_key_env} is not set[/yellow]\n"
            )
            return

        payload = build_sinomis_import_payload(
            date=date,
            markdown=markdown,
            issue_type=issue_type,
        )
        url = urljoin(f"{base_url}/", self.config.import_path.lstrip("/"))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:500]
            self.console.print(
                f"[yellow]⚠️  Sinomis publish failed: HTTP {exc.response.status_code} {body}[/yellow]\n"
            )
            return
        except httpx.HTTPError as exc:
            self.console.print(
                f"[yellow]⚠️  Sinomis publish failed: {exc}[/yellow]\n"
            )
            return

        self.console.print("[green]📨 Published ZH summary to Sinomis AI[/green]\n")

    async def preview_daily_summary(
        self,
        *,
        date: str,
        markdown: str,
        language: str,
        issue_type: str = "daily",
    ) -> dict[str, Any] | None:
        """Ask Sinomis AI to parse a report without saving or sending DingTalk."""
        if language != self.config.language:
            self.console.print(
                f"[yellow]⚠️  Sinomis preview skipped: language {language} does not match {self.config.language}[/yellow]\n"
            )
            return None

        base_url = (self.config.base_url or "").strip().rstrip("/")
        if not base_url or "${" in base_url:
            self.console.print(
                "[yellow]⚠️  Sinomis preview skipped: base_url is not configured[/yellow]\n"
            )
            return None

        api_key = os.environ.get(self.config.management_api_key_env, "").strip()
        if not api_key:
            self.console.print(
                f"[yellow]⚠️  Sinomis preview skipped: {self.config.management_api_key_env} is not set[/yellow]\n"
            )
            return None

        payload = build_sinomis_import_payload(
            date=date,
            markdown=markdown,
            issue_type=issue_type,
            dry_run=True,
        )
        url = urljoin(f"{base_url}/", self.config.import_path.lstrip("/"))
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.config.timeout_sec) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()


def build_sinomis_import_payload(
    *,
    date: str,
    markdown: str,
    issue_type: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build the import payload accepted by sinomis-ai.

    Sinomis can infer most fields from Markdown, but sending a few stable fields
    makes the imported list cards useful even if the Markdown format changes.
    """
    title = infer_title(markdown, date)
    summary = infer_summary(markdown)
    return {
        "dryRun": dry_run,
        "issueType": issue_type,
        "date": date,
        "title": title,
        "summary": summary,
        "contentMarkdown": markdown,
    }


def infer_title(markdown: str, date: str) -> str:
    for line in markdown.splitlines():
        value = line.strip()
        if value.startswith("# "):
            return value.lstrip("#").strip()[:160]
    return f"Horizon AI 日报 - {date}"


def infer_summary(markdown: str) -> str:
    for line in markdown.splitlines():
        value = line.strip()
        if value.startswith(">"):
            return value.lstrip(">").strip()[:500]
    return "Horizon 自动聚合生成的 AI 日报。"
