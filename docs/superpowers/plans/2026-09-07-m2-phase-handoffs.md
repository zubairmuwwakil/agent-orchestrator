# M2 phase handoffs — one session per phase

Each phase below is self-contained: a launch command, a kickoff prompt to paste, and a
done-when. Run them **in order** — Phase 3 changes `PoolConfig.window` to `windows`, which
breaks fixtures written earlier, so phases are not independent even though sessions are.

Plan: [`2026-09-07-m2-multi-vendor-telemetry.md`](2026-09-07-m2-multi-vendor-telemetry.md)
Design and evidence: [`../specs/2026-09-07-m2-design.md`](../specs/2026-09-07-m2-design.md)

Routing follows [`FLEET.md`](../../../FLEET.md): route `(model, effort)`, bump effort on the
same model before changing vendor, and spend the deepest pool that can do the job.

## Quota at time of writing (2026-09-07)

| Pool | 5h | Weekly | Note |
|---|---|---|---|
| `claude` | — | **59% used** | scarcest; reserve it for Phase 3 |
| `codex` | 39% used | 38% used | deepest premium pool right now |
| `antigravity_gemini` | 14% used | 31% used | deepest overall; free `/usage` probe |
| `antigravity_claude` | **75% used** | 25% used | 5h window nearly blocked |

## Routing summary

| Phase | Tasks | Author | Effort | Why |
|---|---|---|---|---|
| 0 | 1 | `agy gemini-3.8-flash-medium` | medium | Pure file creation from content given verbatim |
| 1 | 2–3 | `codex gpt-5.6-terra` | high | Subtle parsing + live CLI iteration; preserves Claude |
| 2 | 4–6 | `claude sonnet` | high | Writes inside `.git`; consequence high, volume low |
| 3 | 7–10 | `claude sonnet` | **xhigh** | The hard one. Escalate to `opus @ high` only if it stalls |
| 4 | 11–12 | `codex gpt-5.6-terra` | high | New vendor contract; author ≠ reviewer (agy reviews) |
| 5 | 13–15 | `codex gpt-5.6-terra` | high | Mechanical, but Typer's callback/subcommand interplay is fiddly |

Phase 3 deliberately starts at `sonnet @ xhigh` rather than `opus @ high`: an effort bump on
the same model is the cheaper first escalation, which is the rule orc itself follows.

---

## Phase 0 — Test infrastructure (Task 1)

**Launch:** `agy --model gemini-3.8-flash-medium`

Nothing here requires judgment: three files whose exact content the plan supplies, plus four
lines of `pyproject.toml`. Do not spend a premium pool on it.

**Kickoff prompt:**

```
Read docs/superpowers/plans/2026-09-07-m2-multi-vendor-telemetry.md and execute
Task 1 only. Stop at its final commit.

The task creates tests/conftest.py, two fixture files, and a pytest marker. All
file contents are given verbatim in the plan — copy them exactly rather than
improvising, since the fixtures are real recorded CLI output and inventing JSON
is the specific mistake this task exists to prevent.

Before committing, run: uv run pytest -q && uv run ruff check . && uv run mypy orc
Do not start Task 2.
```

**Done when:** `uv run pytest -q` still reports 24 passed, `-m live --collect-only` collects nothing, and one commit exists.

---

## Phase 1 — The adapters actually work (Tasks 2–3)

**Launch:** `codex --model gpt-5.6-terra -c model_reasoning_effort="high"`

The two defects that block everything else. Codex authored on codex is fine here: the work is
mechanical parsing against fixtures, and the `live` tests — not a reviewer — are the check.

**Kickoff prompt:**

```
Read AGENTS.md, then docs/superpowers/plans/2026-09-07-m2-multi-vendor-telemetry.md.
Execute Tasks 2 and 3 only, in order, TDD: write the failing test, watch it fail,
implement, watch it pass, commit. Stop after Task 3's commit.

Context you need: the current codex adapter passes both `-s workspace-write` and
`--approve-for-me`, which the CLI rejects outright, so every codex run exits 2. The
existing tests mock subprocess.run and so cannot see it. The claude adapter uses
`--output-format json`, which returns no tool events, so triage is permanently blind.

Both tasks include @pytest.mark.live tests. Run them explicitly with
`uv run pytest -q -m live` — they spend roughly one trivial run per vendor and are
the only check that can catch an argv the CLI refuses.

Never weaken or delete a test to make it pass. Before each commit run:
uv run pytest -q && uv run ruff check . && uv run mypy orc
Do not start Task 4.
```

**Done when:** `uv run pytest -q -m live` passes for both adapters, and the codex parser returns `tool_call_count == 1` against the recorded fixture rather than 2.

---

## Phase 2 — Git safety (Tasks 4–6)

**Launch:** `claude --model sonnet --effort high`

Low volume, high consequence: this phase writes inside `.git` and changes what orc believes
an agent did. Tasks 4 and 5 carry review gates.

**Kickoff prompt:**

```
Read AGENTS.md and SPEC.md §10, then
docs/superpowers/plans/2026-09-07-m2-multi-vendor-telemetry.md.
Execute Tasks 4, 5 and 6 only, in order, TDD. Stop after Task 6's commit.

Tasks 4 and 5 each end with a Review gate step. Run it — the reviewer command is in
the plan's header — and fix every P0 and P1 finding before committing. Record P2/P3
in the commit message. The reviewer must be codex, not Claude: per AGENTS.md an
author is the worst judge of its own diff.

Two things are deliberate and must not be "improved":
- Test tampering keeps its hard abort. Only the enumeration scope changes.
- Appending `.orc/` to .git/info/exclude is the single permitted write inside .git,
  carved out in SPEC §10. Do not touch anything else in .git.

Before each commit: uv run pytest -q && uv run ruff check . && uv run mypy orc
Do not start Task 7.
```

**Done when:** a second `orc` run starts cleanly on a repo that has never gitignored `.orc/`, and `git diff` against the base commit shows work an agent committed.

---

## Phase 3 — Quota telemetry (Tasks 7–10)

**Launch:** `claude --model sonnet --effort xhigh`

The subsystem M2 exists for, and the only phase with genuine design surface: a new contract,
reserve arithmetic every later decision trusts, and a router restructure. Escalate to
`claude --model opus --effort high` only if it stalls — bump effort before changing model.

Run this phase when your Claude weekly window has room; it is the one worth spending it on.

**Kickoff prompt:**

```
Read AGENTS.md, SPEC.md §5 and §7, and
docs/superpowers/specs/2026-09-07-m2-design.md §2 for the evidence behind the design.
Then read docs/superpowers/plans/2026-09-07-m2-multi-vendor-telemetry.md and execute
Tasks 7, 8, 9 and 10 only, in order, TDD. Stop after Task 10's commit.

Design points that are decided, not open:
- Telemetry is authoritative; the flat estimate is a fallback for pools without it.
- The three vendors disagree on polarity — claude reports utilization, codex reports
  used_percent, agy reports remaining_fraction. QuotaWindow stores USED. Get the
  inversion right; it is the easiest thing here to get silently backwards.
- A pool is ineligible once ANY of its windows reaches 1 - reserve_fraction, until
  that window's own resets_at. Reserve is evaluated before a run, never mid-ladder.
- Quota filters eligibility only, never preference. Candidate order stays as
  configured, so the ladder logs stay clean routing data.
- `volume` is a fallback entered only when every ladder rung is blocked — never a rung.

Task 9 changes PoolConfig.window to windows, so every existing test fixture needs
updating in that same commit. Expect tests/test_ledger.py and tests/test_router.py
to need edits.

Tasks 9 and 10 carry Review gates. Run them with codex and fix P0/P1 before
committing. For Task 10, ask the reviewer specifically whether any path can spend a
pool the ledger judged ineligible.

Before each commit: uv run pytest -q && uv run ruff check . && uv run mypy orc
Do not start Task 11.
```

**Done when:** a pool over its reserve is skipped without spending a run, `--use-reserve` overrides it, and a fully blocked ladder degrades to `volume` instead of failing.

---

## Phase 4 — Antigravity adapter (Tasks 11–12)

**Launch:** `codex --model gpt-5.6-terra -c model_reasoning_effort="high"`

Authored by codex so that `agy` can review its own vendor contract with fresh eyes.

**Kickoff prompt:**

```
Read AGENTS.md and docs/superpowers/specs/2026-09-07-m2-design.md §3 for the verified
agy contract. Then read docs/superpowers/plans/2026-09-07-m2-multi-vendor-telemetry.md
and execute Tasks 11 and 12 only, in order, TDD. Stop after Task 12's commit.

Task 11 Step 1 captures a REAL agy stream-json fixture before any parser exists. Do
that first and write the assertions against what actually comes back. The event names
in the plan come from vendor docs and have not been verified against the binary —
reconcile them. This ordering is the whole point: trusting a plausible flag
combination from docs is what shipped a fatal codex defect.

Three verified properties of agy 1.1.27 that differ from the other CLIs:
- No working-directory flag. cwd comes from the subprocess, plus --add-dir.
- Permissions are --mode accept-edits, not --permission-mode.
- The reasoning tier is IN the model slug (gemini-3.8-flash-high). `agy models` lists
  no bare slug, so a candidate's @effort composes into the slug and clamps when the
  ladder bumps past low/medium/high. Do not pass --effort.

Task 11 carries a Review gate using agy itself as reviewer. Run it, fix P0/P1.

Before each commit: uv run pytest -q && uv run ruff check . && uv run mypy orc
Do not start Task 13.
```

**Done when:** `uv run pytest -q -m live tests/test_antigravity.py` passes and `orc quota` lists `antigravity_gemini` with observed numbers.

---

## Phase 5 — Logging, discovery, CLI (Tasks 13–15)

**Launch:** `codex --model gpt-5.6-terra -c model_reasoning_effort="high"`

Mostly mechanical, but Typer's callback-plus-subcommand interplay is exactly what the
existing `sys.argv` hack was avoiding, so it is worth `high` rather than `medium`.

**Kickoff prompt:**

```
Read AGENTS.md, SPEC.md §11 and §13, then
docs/superpowers/plans/2026-09-07-m2-multi-vendor-telemetry.md.
Execute Tasks 13, 14 and 15 only, in order, TDD. Stop after Task 15's commit.

Task 15 must keep BOTH invocation styles working: `orc "fix the auth test"` from
SPEC §1, and `orc quota` / `orc log --last` from SPEC §11. One Typer app whose
callback owns the bare task string, with real registered subcommands. Delete the
sys.argv dispatch and the second Typer app.

SPEC §13 forbids prompts in log.jsonl. Store prompt_hash. Nothing is lost, because
§10 already persists the full prompt to .orc/runs/<id>/prompt-N.txt, which is what
`orc log` reads for display.

Before each commit: uv run pytest -q && uv run ruff check . && uv run mypy orc
When Task 15 is committed, M2 is complete. Verify every acceptance checkbox in
SPEC.md §12 against the Acceptance Mapping table at the end of the plan, then report
which are met and which are not. Do not start M3.
```

**Done when:** every SPEC §12 M2 acceptance checkbox is demonstrably met.

---

## If a phase stalls

Follow orc's own triage rule rather than reaching for a bigger model reflexively:

- **Skipped work** — never ran the tests, few tool calls, claimed success while the check
  fails → same model, **effort +1**.
- **Genuinely iterated and still failed** — ran the checks repeatedly, edited files, still
  red → **change vendor**, to the next row in the routing summary.

Never let a stalled session weaken a test to go green. That rule holds in this repo and in
any target repo, and no time pressure changes it.
