"""Typer entrypoint for M2's coding flow, quota ledger, and run log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from typer.core import TyperGroup, _click

from orc.adapters.antigravity import AntigravityAdapter
from orc.adapters.base import AgentAdapter
from orc.adapters.claude_code import ClaudeCodeAdapter
from orc.adapters.codex import CodexAdapter
from orc.config import ConfigError, OrcConfig, find_config, load_config
from orc.gitops import SafetyError
from orc.ledger import Ledger
from orc.report import format_quota, format_run
from orc.router import run_task


class BareTaskGroup(TyperGroup):
    """Route registered names to commands and any other trailing text to the callback.

    Click normally makes an argument on a group consume a command name. Keeping the
    bare task in ``ctx.args`` instead lets a single Typer app support both ``orc
    "task"`` and registered commands such as ``orc quota``.
    """

    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        parsed = super().parse_args(ctx, args)
        tokens = [*ctx._protected_args, *ctx.args]
        if tokens and self.get_command(ctx, tokens[0]) is None:
            ctx._protected_args = []
            ctx.args = tokens
            return ctx.args
        return parsed


app = typer.Typer(
    add_completion=False,
    cls=BareTaskGroup,
    context_settings={"allow_extra_args": True},
    invoke_without_command=True,
)
console = Console()


def _adapters(config: OrcConfig) -> dict[str, AgentAdapter]:
    """Construct the configured M2 adapters without probing or running them."""
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
    return adapters


def _load_target_config(target: Path) -> tuple[Path, OrcConfig]:
    """Resolve a target and its configuration, preserving CLI-friendly errors."""
    resolved_target = target.resolve()
    return resolved_target, load_config(find_config(resolved_target))


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    target: Annotated[Path, typer.Option(help="Git repository to work in.")] = Path("."),
    lane: Annotated[str | None, typer.Option(help="Start rung: volume, standard, quality.")] = None,
    agent: Annotated[str | None, typer.Option(help="Force one candidate, as vendor:model.")] = None,
    effort: Annotated[str | None, typer.Option(help="Force the starting effort level.")] = None,
    use_reserve: Annotated[
        bool, typer.Option(help="Spend into a pool's reserved headroom.")
    ] = False,
    allow_destructive: Annotated[
        bool, typer.Option(help="Allow a task matching the destructive-operation denylist.")
    ] = False,
) -> None:
    """Run a coding agent ladder on TASK, then verify it independently."""
    if ctx.invoked_subcommand is not None:
        return
    task = " ".join(ctx.args).strip() or None
    if task is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)

    try:
        resolved_target, config = _load_target_config(target)
    except (ConfigError, KeyError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error

    adapters = _adapters(config)
    if not any(adapter.available() for adapter in adapters.values()):
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
            use_reserve=use_reserve,
            start_lane=lane,
            agent=agent,
            effort=effort,
        )
    except (SafetyError, ValueError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error

    console.print(format_run(task_run))
    if not task_run.verified:
        raise typer.Exit(code=1)


@app.command()
def quota(
    target: Annotated[Path, typer.Option(help="Git repository whose ledger to read.")] = Path("."),
) -> None:
    """Print each pool's utilization, reset time, and telemetry source."""
    try:
        resolved_target, config = _load_target_config(target)
    except (ConfigError, KeyError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=2) from error

    ledger = Ledger(resolved_target / ".orc" / "ledger.json")
    for vendor, adapter in _adapters(config).items():
        pool_id = config.adapters[vendor].pool
        if pool_id is None:
            continue
        observation = adapter.quota_probe()
        if observation is not None:
            ledger.record_observation(pool_id, observation)
    console.print(format_quota(ledger.status(config.pools)))


@app.command("log")
def show_log(
    target: Annotated[Path, typer.Option(help="Git repository whose run log to show.")] = Path("."),
    last: Annotated[int, typer.Option(help="How many recent runs to show.", min=1)] = 1,
) -> None:
    """Summarize recent runs and display their stored prompts and artifact paths."""
    resolved_target = target.resolve()
    log_file = resolved_target / ".orc" / "log.jsonl"
    if not log_file.is_file():
        typer.echo("no recorded runs")
        return

    entries: list[dict[str, object]] = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)

    for entry in entries[-last:]:
        task_id = entry.get("task_id", "unknown")
        run_dir = resolved_target / ".orc" / "runs" / str(task_id)
        prompts = sorted(run_dir.glob("prompt-*.txt")) if run_dir.is_dir() else []
        typer.echo(f"task {task_id}: {entry.get('outcome', 'unknown')}")
        if prompts:
            typer.echo(f"prompt:\n{prompts[-1].read_text(encoding='utf-8').strip()}")
        typer.echo(f"artifacts: {run_dir}")


def main() -> None:
    """Console-script entrypoint for the one Typer application."""
    app()
