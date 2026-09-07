# `orc` agent router

`orc` is a quota-aware CLI that routes coding tasks across AI CLI subscriptions,
verifies work, and cross-reviews with a different vendor. It must not embed vendor
SDKs or own target-repo code; adapters wrap official CLIs and work stays in the target.
`SPEC.md` is the source of truth and outranks every other document in this repo.

## Commands

| Task | Command |
|---|---|
| Install | `uv sync` |
| Check | `uv run pytest -q && uv run ruff check . && uv run mypy orc` |
| Format | `uv run ruff format .` |

## Hard rules

- Build only the current `SPEC.md` milestone; do not start the next one unprompted.
- Never weaken, skip, or delete a test to make it pass.
- Missing or unauthenticated CLIs degrade to warnings and skipped lanes, never crashes.
- Discover each CLI from `--help` and current docs before implementing its adapter.
- Keep subprocesses in `adapters/`, `verify.py`, or `gitops.py`; keep model names in
  `orc.toml`, never Python. Never commit secrets.
- Changes to this repo obey [`FLEET.md`](FLEET.md) rule 4 too. A high-blast-radius diff —
  quota accounting, git safety, or a vendor contract that later work will trust without
  re-deriving — is reviewed by a vendor other than its author, given the task, diff, and
  verification output but never the author's transcript. Where an automated check can
  settle the question instead, write the check and skip the review: a fatal adapter flag
  conflict once shipped green because its author also wrote its tests.

## Read when you are…

| File | …doing this |
|---|---|
| [`SPEC.md`](SPEC.md) | writing code or changing behavior — read it in full |
| [`development.md`](docs/policies/development.md) | changing Python, tests, adapters, verification, gitops, state, or dependencies |
| [`FLEET.md`](FLEET.md) | choosing or changing a model, effort, pool, lane, or reviewer |

## Freedom

Anything not named here and not caught by `uv run pytest -q && uv run ruff check . && uv run mypy orc` is yours to decide. Prefer acting and letting the check fail over asking.
