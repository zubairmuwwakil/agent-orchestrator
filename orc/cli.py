"""Typer entrypoint for Milestone 2's multi-vendor coding flow, plus `orc quota`."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from orc.adapters.antigravity import AntigravityAdapter
from orc.adapters.base import AgentAdapter
from orc.adapters.claude_code import ClaudeCodeAdapter
from orc.adapters.codex import CodexAdapter
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
    """Run coding agent ladder on TASK in TARGET, then independently verify it."""
    resolved_target = target.resolve()
    try:
        config = load_config(find_config(resolved_target))
    except (ConfigError, KeyError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error

    adapters: dict[str, AgentAdapter] = {}
    if "claude" in config.adapters:
        adapters["claude"] = ClaudeCodeAdapter(config.adapters["claude"])
    if "codex" in config.adapters:
        adapters["codex"] = CodexAdapter(config.adapters["codex"])
    if "antigravity" in config.adapters:
        adapter_config = config.adapters["antigravity"]
        pool_config = config.pools.get(adapter_config.pool or "")
        adapters["antigravity"] = AntigravityAdapter(
            adapter_config,
            quota_group=pool_config.quota_group if pool_config is not None else None,
        )

    available_adapters = {k: v for k, v in adapters.items() if v.available()}
    if not available_adapters:
        typer.echo(
            "error: No configured agent CLIs are installed and authenticated (checked: "
            + ", ".join(adapters.keys())
            + ").",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        task_run = run_task(
            task,
            resolved_target,
            config,
            adapters=adapters,
            allow_destructive=allow_destructive,
        )
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
