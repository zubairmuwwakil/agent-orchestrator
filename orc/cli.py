"""Typer entrypoint for Milestone 1's single-lane coding flow, plus `orc quota`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from orc.adapters.claude_code import ClaudeCodeAdapter
from orc.config import ConfigError, find_config, load_config
from orc.gitops import SafetyError
from orc.ledger import Ledger
from orc.report import format_quota, format_run
from orc.router import run_task

app = typer.Typer(add_completion=False, invoke_without_command=True, no_args_is_help=True)
quota_app = typer.Typer(add_completion=False)
console = Console()


@app.callback()
def run(
    task: Annotated[str, typer.Argument(help="Coding task to perform.")],
    target: Annotated[Path, typer.Option(help="Git repository to work in.")] = Path("."),
    allow_destructive: Annotated[
        bool, typer.Option(help="Allow a task matching the destructive-operation denylist.")
    ] = False,
) -> None:
    """Run Claude Code on TASK in TARGET, then independently verify it."""
    resolved_target = target.resolve()
    try:
        config = load_config(find_config(resolved_target))
        claude_config = config.adapters["claude"]
    except (ConfigError, KeyError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error
    adapter = ClaudeCodeAdapter(claude_config)
    if not adapter.available():
        typer.echo(
            "error: Claude CLI is missing or not authenticated; install and sign in to `claude`.",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        task_run = run_task(task, resolved_target, config, adapter, allow_destructive)
    except (SafetyError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error
    console.print(format_run(task_run))
    if not task_run.verified:
        raise typer.Exit(code=1)


@quota_app.command()
def quota(
    target: Annotated[Path, typer.Option(help="Git repository whose ledger to read.")] = Path("."),
) -> None:
    """Print the advisory quota ledger: pool, window, est. remaining, exhausted-until."""
    resolved_target = target.resolve()
    try:
        config = load_config(find_config(resolved_target))
    except ConfigError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error
    ledger = Ledger(resolved_target / ".orc" / "ledger.json")
    console.print(format_quota(ledger.status(config.pools)))


def main() -> None:
    """Console-script entrypoint; routes `quota` before `app`'s TASK argument can claim it."""
    if sys.argv[1:2] == ["quota"]:
        quota_app(args=sys.argv[2:])
    else:
        app()
