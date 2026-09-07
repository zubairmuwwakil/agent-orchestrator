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
  destructive-pattern blocking, and no writes outside the target repository. The one
  permitted write inside `.git` is appending `.orc/` to `.git/info/exclude`, which is
  local-only and never committed; nothing else in `.git` may be touched.
- Collect diffs against the base commit recorded at branch creation, so work an agent
  committed or staged is not missed. Enumerate files with `git ls-files`, never a
  filesystem walk that can wander into `.venv/` or `node_modules/`.
- Keep activity trails to decisions, actions, results, and diffs; exclude chain-of-thought.
- Use fake adapters and synthetic `AgentResult` values in router, triage, and ledger
  tests. These must not call live APIs.
- Test adapters at both halves of the subprocess boundary. Parse against real CLI output
  recorded under `tests/fixtures/`, never against invented JSON — mocking `subprocess.run`
  validates the parser while leaving the argv untested, which is how a fatal flag conflict
  shipped with a green suite. Validate argv with `@pytest.mark.live` tests that run the real
  CLI on a trivial prompt; these are deselected by default and are the sole exception to the
  no-live-APIs rule. Asserting argv against a hand-written expectation is not a substitute.
- Take agent timeouts from `[agents]` and verification timeouts from `[verify]`; never share
  one budget. Pass `stdin=DEVNULL` to every adapter subprocess.
- Keep `examples/sample-python/` as the under-50-line acceptance fixture with one
  intentionally failing test.
- Give rate-limit handling, triage heuristics, and verification-command detection
  dedicated unit tests.

Definition of done: the router's Check command passes and the current milestone's
acceptance criteria are demonstrably met.
