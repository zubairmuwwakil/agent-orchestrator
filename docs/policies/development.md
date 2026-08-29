# Development policy

`SPEC.md` owns product behavior, architecture, milestone order, and safety rails. Its
decisions are fixed: implement the current milestone and report concerns rather than
silently deviating. If a necessary choice is genuinely ambiguous, ask when attended;
otherwise choose the simplest option, mark it `# SPEC-GAP:`, and report it.

## Code and dependencies

- Support Python 3.12+ with `uv`. Runtime dependencies are limited to Typer, Pydantic,
  and Rich; ask before adding another runtime dependency or a test dependency beyond
  pytest.
- Use type hints throughout and small modules matching the layout in `SPEC.md`; avoid
  god objects.
- Keep subprocess calls inside `orc/adapters/`, `orc/verify.py`, or `orc/gitops.py`.
- Keep model names, effort levels, lane orders, estimates, and error patterns in
  `orc.toml`, never Python.
- Store runtime state under the target repository's gitignored `.orc/` directory.
  Never write elsewhere in the target or outside it.
- Never commit secrets, tokens, or API keys. Adapters use each CLI's own authentication.

## Safety and verification

- Never weaken, skip, or delete a test to make it pass in this or a target repository.
- Adapters must treat a missing or unauthenticated CLI as a warning and skipped lane,
  never a crash.
- Discover every CLI from its current `--help` output and documentation; do not trust
  remembered flags or illustrative commands in `SPEC.md`.
- Apply the git safety rails in `SPEC.md` §10: branch-only work, no force pushes,
  destructive-pattern blocking, and no writes outside the target repository.
- Keep activity trails to decisions, actions, results, and diffs; exclude chain-of-thought.
- Use fake adapters and synthetic `AgentResult` values in router, triage, and ledger
  tests. Tests must not call live APIs.
- Keep `examples/sample-python/` as the under-50-line acceptance fixture with one
  intentionally failing test.
- Give rate-limit handling, triage heuristics, and verification-command detection
  dedicated unit tests.

Definition of done: the router's Check command passes and the current milestone's
acceptance criteria are demonstrably met.
