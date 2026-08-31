"""Project-aware verification command detection and execution."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from orc.config import VerifyConfig


@dataclass(slots=True)
class VerificationPlan:
    tests: list[str]
    lint: list[str]
    build: list[str]


@dataclass(slots=True)
class VerificationResult:
    ok: bool
    partial: bool
    tests_detected: bool
    output: str
    plan: VerificationPlan


def detect_commands(target: Path, overrides: VerifyConfig) -> VerificationPlan:
    """Apply target instructions, configured overrides, then project detection."""
    instructed = _commands_from_target_instructions(target)
    if instructed is not None:
        return instructed
    if overrides.tests or overrides.lint or overrides.build:
        return VerificationPlan(overrides.tests, overrides.lint, overrides.build)
    return _auto_detect(target)


def run_verification(target: Path, plan: VerificationPlan, timeout_s: int) -> VerificationResult:
    """Run the selected commands with captured output and no shell interpolation."""
    results: list[str] = []
    successful = True
    for kind, commands in (("test", plan.tests), ("lint", plan.lint), ("build", plan.build)):
        for command in commands:
            try:
                arguments = shlex.split(command)
                # `orc` may be launched directly from its isolated venv, whose
                # tools are not on PATH. Preserve the configured command while
                # using the current interpreter as a narrow fallback.
                if (
                    arguments
                    and arguments[0] in {"pytest", "ruff", "mypy"}
                    and shutil.which(arguments[0]) is None
                ):
                    arguments = [sys.executable, "-m", *arguments]
                completed = subprocess.run(
                    arguments,
                    cwd=target,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
                command_output = (completed.stdout + completed.stderr).strip()
                results.append(f"$ {command}\n{command_output}".rstrip())
                if completed.returncode != 0:
                    successful = False
            except (OSError, subprocess.TimeoutExpired) as error:
                successful = False
                results.append(f"$ {command}\n{error}")
    tests_detected = bool(plan.tests)
    partial = not tests_detected
    if partial and not (plan.lint or plan.build):
        results.append("No tests detected; no lint or build command was detected either.")
    elif partial:
        results.append("No tests detected; completed lint/build verification only.")
    return VerificationResult(successful, partial, tests_detected, "\n\n".join(results), plan)


def _commands_from_target_instructions(target: Path) -> VerificationPlan | None:
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = target / name
        if not path.is_file():
            continue
        commands = _fenced_commands_under_testing_heading(path.read_text(encoding="utf-8"))
        if commands:
            return _classify(commands)
    return None


def _fenced_commands_under_testing_heading(contents: str) -> list[str]:
    heading = re.search(r"(?im)^#{1,6}\s+(?:commands|testing)\b.*$", contents)
    if heading is None:
        return []
    remainder = contents[heading.end() :]
    next_heading = re.search(r"(?m)^#{1,6}\s+", remainder)
    section = remainder[: next_heading.start()] if next_heading else remainder
    blocks = re.findall(r"(?ms)^```[^\n]*\n(.*?)^```", section)
    lines = (line.strip() for block in blocks for line in block.splitlines())
    return [command for command in (_strip_inline_comment(line) for line in lines) if command]


def _strip_inline_comment(line: str) -> str:
    """Drop a trailing `# ...` shell-style comment, e.g. from a documented command list."""
    if not line:
        return line
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        return line
    return shlex.join(tokens)


def _classify(commands: list[str]) -> VerificationPlan:
    tests: list[str] = []
    lint: list[str] = []
    build: list[str] = []
    for command in commands:
        lower = command.casefold()
        if "test" in lower or "pytest" in lower:
            tests.append(command)
        elif any(word in lower for word in ("ruff", "lint", "mypy", "typecheck", "vet", "clippy")):
            lint.append(command)
        elif "build" in lower or "verify" in lower:
            build.append(command)
    return VerificationPlan(tests, lint, build)


def _auto_detect(target: Path) -> VerificationPlan:
    package_json = target / "package.json"
    if package_json.is_file():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        return VerificationPlan(
            ["npm test"] if "test" in scripts else [],
            [f"npm run {name}" for name in ("lint", "typecheck") if name in scripts],
            ["npm run build"] if "build" in scripts else [],
        )
    if (target / "pom.xml").exists() or (target / "mvnw").exists():
        mvn = "./mvnw" if (target / "mvnw").exists() else "mvn"
        return VerificationPlan([f"{mvn} -q test"], [], [f"{mvn} -q clean verify"])
    if (target / "go.mod").exists():
        return VerificationPlan(["go test ./..."], ["go vet ./..."], ["go build ./..."])
    if (target / "Cargo.toml").exists():
        return VerificationPlan(["cargo test -q"], ["cargo clippy -q"], ["cargo build -q"])
    has_python = (
        (target / "pyproject.toml").exists()
        or (target / "pytest.ini").exists()
        or any(target.rglob("*.py"))
    )
    if has_python:
        has_tests = (target / "pytest.ini").exists() or any(
            path.name.startswith("test_") or path.name.endswith("_test.py")
            for path in target.rglob("*.py")
        )
        mypy_configured = (target / "mypy.ini").exists() or (target / ".mypy.ini").exists()
        return VerificationPlan(
            ["pytest -q"] if has_tests else [],
            ["ruff check ."] + (["mypy ."] if mypy_configured else []),
            [],
        )
    return VerificationPlan([], [], [])
