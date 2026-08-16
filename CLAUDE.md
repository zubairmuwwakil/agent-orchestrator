# CLAUDE.md — `orc` repository

## What this project is

`orc` is a quota-aware CLI orchestrator that routes coding tasks across the owner's AI subscriptions (Claude Code, Codex, Antigravity, Copilot) via subprocess adapters, verifies results with real tests, and cross-reviews with a different vendor. **`SPEC.md` is the source of truth — read it fully before writing any code.** If this file and SPEC.md ever conflict, SPEC.md wins.

## Build discipline

- Build only the **current milestone** (SPEC §12), in order. Stop at each acceptance checklist and report; do not start the next milestone unprompted.
- Decisions in SPEC §2 are fixed. If you believe one is wrong, finish the milestone as specified and raise the concern in your final report with your reasoning — do not silently deviate.
- If the spec is genuinely ambiguous on something you must decide now, ask; if unattended, choose the simplest option, mark it `# SPEC-GAP:` in code, and list it in your report.

## Commands

```
uv sync                 # install/refresh deps
uv run pytest -q        # tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy orc         # types (permissive config is fine initially)
```

Definition of done for any change: tests pass, ruff clean, mypy clean, relevant acceptance boxes demonstrably met.

## Conventions

- Python ≥3.12, `uv`-managed. Runtime deps are **typer, pydantic, rich** only — ask before adding anything else (including for tests beyond pytest).
- Type hints everywhere; small modules matching the SPEC §4 layout; no god-objects.
- `subprocess` calls live **only** in `adapters/` and `verify.py`/`gitops.py`. Nothing else shells out.
- All model names, effort levels, lane orders, estimates, and error-string patterns live in `orc.toml` — never hardcode a model name in Python.
- Runtime state goes under `.orc/` (gitignored). Never write outside the repo or the target repo's `.orc/`.
- No secrets, tokens, or API keys anywhere in this repo. Adapters rely on each CLI's own auth.

## Hard rules (non-negotiable)

- Never weaken, skip, or delete a test to make it pass — in this repo or any target repo.
- Adapters must degrade gracefully: a missing or unauthenticated CLI is a warning and a skipped lane, never a crash.
- Discovery-first for every CLI: probe `--help` and current docs at implementation time; do not trust flag names from memory or from SPEC examples.
- Respect SPEC §10 safety rails in all gitops code (branch-only work, no force-push, destructive-pattern denylist).
- Keep the activity trail free of chain-of-thought: decisions, actions, results, diffs only.

## Testing approach

- Fake adapters (synthetic `AgentResult`s) for router/triage/ledger tests — no real API calls in the test suite.
- `examples/sample-python/` is the acceptance fixture: a tiny package with one intentionally failing test. Keep it under 50 lines.
- Rate-limit handling, triage heuristics, and verify-command detection each get dedicated unit tests.

## For other agents

If Codex or Antigravity is pointed at this repo, symlink this file: `ln -s CLAUDE.md AGENTS.md`.
