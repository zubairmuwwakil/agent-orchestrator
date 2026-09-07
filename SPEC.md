# SPEC.md — `orc`: a quota-aware multi-model coding orchestrator (MVP)

**Status:** Decided — build-ready. Do not relitigate §2 decisions; flag concerns in the final report instead.
**Owner:** solo developer. **Date:** 2026-08-15.
**Companion file:** `CLAUDE.md` (repo conventions and hard rules for the building agent).

---

## 1. Problem & north star

The owner has four AI coding subscriptions (Claude Pro, ChatGPT Plus, Antigravity Pro, Copilot Pro) and wants them to behave like **one cohesive coding agent**: give the right task to the right (model, effort) pair with the right context, verify results independently, and never silently burn a scarce quota pool on work a cheap pool could do.

One-line UX:

```
orc "fix the flaky auth test"
```

**North star:** cost-aware escalation + independent verification, not clever up-front routing.

---

## 2. Decisions already made (the "why" lives in project notes — treat these as fixed)

1. **No up-front task classifier.** Routing = escalation ladder + verification. Classification guesses; verification knows. Ladder logs become routing data for v2.
2. **The routing unit is `(model, effort)`, not model.** An effort bump on the same model is always the first escalation step.
3. **Failure triage — the "lazy vs dumb" rule.** If the agent *skipped work* (never ran tests, few tool calls, bailed early, claimed success without evidence) → retry **same model, higher effort**. If it *genuinely iterated* (ran tests ≥2×, edited files) and still failed → **escalate to the next rung**.
4. **The quota ledger is MVP core, not a v2 feature.** All four subscriptions are entry-tier; quota is the binding constraint.
   *Amended 2026-09-07:* the original rationale assumed no product exposes usage. Claude Code,
   Codex, and Antigravity all report real utilization (§7). The ledger reads telemetry first and
   estimates only as a fallback.
5. **Adapters are subprocess wrappers around official CLIs** (`claude`, `codex`, Antigravity CLI, Copilot CLI). No MCP-as-transport, no vendor SDK lock-in, in v1.
6. **Expensive models are consultants, not agents.** Opus 5 (xhigh+), Sol (xhigh/ultra), and Fable 5 are invoked in single-shot "consult" mode with a prepared context package — never in open-ended tool loops. Cheap/mid models do the looping.
7. **Cross-vendor review with fresh context.** Reviewer vendor ≠ author vendor. Reviewer sees task + diff + verification results — never the author's transcript. Findings are P0–P3; fix P0/P1, report P2/P3.
8. **All routing tables live in `orc.toml`, never in code.** Model rankings go stale monthly.
9. **Do not build adapters or config entries for GPT-5.4 / GPT-5.4-mini** — they leave Codex on 2026-08-31.
10. **Fable 5 is pay-per-token on the owner's plan.** Consultant-only, gated behind an interactive cost confirmation, with estimated cost logged. (Owner has a promo credit expiring 2026-09-17.)

---

## 3. The fleet (owner's actual access — encode as `orc.toml` defaults)

| Pool id | Models (effort levels) | Windows | Default role |
|---|---|---|---|
| `claude_pro` | Sonnet 5, Opus 5, Opus 4.8/4.7/4.6, Haiku 4.5 (low/medium/high/xhigh; max/ultracode session-only) | 5h + weekly | Quality lane; Opus 5 = strongest included model |
| `codex_plus` | GPT-5.6 Sol, Terra, Luna (light/medium/high/xhigh; ultra on Sol+Terra) | weekly (5h currently lifted) | Luna = volume, Terra = standard, Sol = escalation |
| `antigravity_gemini` | Gemini 3.7 Flash, Gemini 3.1 Pro (low/medium/high) | combined pool, drawn down at API-price ratio; 5h + weekly | Deepest pool; Flash = default cheap agent lane |
| `antigravity_claude` | Claude Sonnet 4.6, Opus 4.6, GPT-OSS 120B | separate small fixed pool | Shallow — reserve for browser-verified flows; not in default ladder |
| `copilot` | Base models (0x, unlimited); premium: Haiku 0.33x, Sonnet 1x, Opus 3x, GPT-5.6 family | monthly allowance | Free lane + budget reviewer lane. Never use Copilot's built-in code-review feature (13x). |
| `fable_paid` | Fable 5 (usage credits) | pay-per-token | Consultant of last resort, confirmation-gated |

Assume the numbers above may already be stale — they are config defaults, not code.

---

## 4. Architecture

```
            ┌────────────────────────────────────────────┐
 user ──▶ cli ──▶ TaskRun ──▶ Router (ladder × ledger) ──▶ Adapter.run()
            │                                    │             │
            │                                    ▼             ▼
            │                              Ledger (.orc/)   target repo (branch)
            │                                                  │
            └──◀ Reporter ◀── Reviewer (M3) ◀── Verifier ◀────┘
```

Repo layout:

```
orc/
  cli.py            # typer entrypoint: run / quota / log / consult
  config.py         # pydantic models; loads + validates orc.toml
  ledger.py         # quota state in .orc/ledger.json
  router.py         # lane selection, triage, escalation
  adapters/
    base.py         # AgentAdapter ABC + AgentRequest/AgentResult
    claude_code.py  # M1
    codex.py        # M2
    antigravity.py  # M2 (agy)
    copilot.py      # M4
  verify.py         # project detection + test/lint/build harness
  review.py         # cross-vendor review + adjudication (M3)
  contextpack.py    # consultant context packages (M3)
  gitops.py         # branch/safety helpers
  report.py         # activity trail + final summary (rich)
examples/
  sample-python/    # tiny fixture repo with one seeded failing test (for acceptance)
.orc/               # runtime state, gitignored: ledger.json, log.jsonl, runs/<id>/
orc.toml            # pools, lanes, ladder, estimates, verify commands
```

**Stack (fixed):** Python ≥3.12, `uv`, `typer`, `pydantic`, `rich`. Tests `pytest`, lint `ruff`, types `mypy` (permissive to start). No other runtime dependencies without asking.

**Trade-offs accepted:** subprocess adapters are slower and lossier than SDKs but vendor-neutral and swappable; quota is read from vendor telemetry where available and self-estimated otherwise (§7); Python over Go/TS for one-developer iteration speed.

---

## 5. Adapter contract

```python
class AgentAdapter(ABC):
    name: str  # "claude", "codex", ...

    def available(self) -> bool: ...  # CLI on PATH + authenticated
    def run(self, req: AgentRequest) -> AgentResult: ...

    def quota_probe(self) -> QuotaObservation | None:
        return None  # no telemetry available; the ledger falls back to estimates


@dataclass
class AgentRequest:
    prompt: str
    mode: Literal["agent", "consult"]  # consult = single shot, tools off where CLI allows
    model: str
    effort: str
    cwd: Path
    timeout_s: int
    context_files: list[Path] | None = None  # consult mode only


@dataclass
class AgentResult:
    status: Literal["ok", "fail", "rate_limited", "timeout", "error", "unavailable"]
    text: str
    usage: dict | None  # tokens if the CLI reports them, else None
    transcript_path: Path
    tool_call_count: int | None  # for triage heuristics, if derivable
    ran_commands: list[str]  # for triage heuristics, if derivable
    quota: QuotaObservation | None  # utilization observed during this run


@dataclass
class QuotaWindow:
    kind: Literal["5h", "weekly", "monthly"]
    used_fraction: float
    resets_at: datetime


@dataclass
class QuotaObservation:
    windows: list[QuotaWindow]  # a pool may have several simultaneous windows
    observed_at: datetime
    source: Literal["stream", "session-file", "command"]
```

Rules for every adapter:

- **Discovery first.** The first implementation task per adapter is to run `<cli> --help` and read current official docs. Do not trust flag names from this spec — they drift. Known starting points only: `claude -p/--print` with JSON output, `--model`, `--effort`; `codex exec` for non-interactive runs; `agy -p/--print` with `--model`, `--effort` and `--output-format` (headless confirmed 2026-09-07, flags still to be re-verified against `agy --help`); Copilot CLI (capabilities unknown, treat as a spike).
- Detect rate-limit/quota errors from exit codes + stderr patterns and return `rate_limited` so the ledger can mark the pool exhausted. Collect the real error strings during the discovery spike; keep the patterns in config.
- `consult` mode: one completion, no agentic tool loop. If a CLI cannot disable tools, constrain via prompt + lowest-permission flags, and cap `timeout_s` low.
- Missing/unauthenticated CLI → `available() == False`; router skips the lane and warns once per run. Never crash because a vendor is absent.
- Diffs come from `gitops` (git itself), never parsed from agent output. Diff against the
  base commit recorded at branch creation, so committed and staged work is not missed.
- Each adapter owns its own quota mechanism and normalizes it to `QuotaObservation`.
  `ledger.py` stays vendor-agnostic. Missing telemetry is not an error.
- Adapters set `stdin=DEVNULL`; agent timeouts come from `[agents]`, never from `[verify]`.
- Adapter tests validate **both** halves of the subprocess boundary: parsing, against real
  recorded output in `tests/fixtures/`; and argv, via opt-in `live` tests that run the real
  CLI. Asserting argv against a hand-written expectation is not sufficient.

---

## 6. Router: lanes, ladder, triage

`orc.toml` defaults for the owner (illustrative — validate against config schema):

```toml
[lanes.volume]
candidates = ["antigravity:gemini-3.7-flash@medium", "codex:luna@medium"]

[lanes.standard]
candidates = ["codex:terra@high", "claude:sonnet-5@high"]

[lanes.quality]
candidates = ["claude:opus-5@high", "codex:sol@high"]

[lanes.consultant]
candidates = ["claude:opus-5@xhigh", "codex:sol@ultra", "fable_paid:fable-5@high"]

[ladder]
order = ["standard", "quality"]     # M1–M2 default path
fallback = "volume"                 # reached only when every rung is reserve-blocked
attempts_per_rung = 2
max_total_attempts = 4
```

- Default start rung: `standard`. Overrides: `--lane volume|standard|quality`, `--agent vendor:model`, `--effort <level>`, `--no-review`, `--use-reserve`.
- Within a lane, pick the first candidate whose pool is eligible (ledger) and whose adapter is available.
  Quota decides **eligibility only, never preference** — candidate order stays as configured, so ladder
  logs remain clean routing data for v2.
- `volume` is a fallback beside the ladder, not a rung below it. It is reached only when every
  candidate in every rung is blocked by the reserve line, turning exhaustion into a degraded route
  instead of a failed run. A cheap first rung would consume the fixed `max_total_attempts` budget and
  starve `quality`.
- **Triage after each failed verification (heuristics, no ML):**
  - *Lazy signals* → same model, effort +1: verify command never appeared in `ran_commands`; `tool_call_count` below a config floor; success claimed while verification fails.
  - *Dumb signals* → next rung: verification ran ≥2 times, files were edited, still failing.
  - Ambiguous → treat as lazy first (cheaper).
- Hard stop at `max_total_attempts`: surface best attempt, failing output, and transcripts to the human. Never loop forever.

---

## 7. Quota ledger

*Revised 2026-09-07.* Three of the vendors report real utilization; the ledger reads it and falls
back to estimates only where it is absent.

**Telemetry sources** (each adapter normalizes to `QuotaObservation`, §5):

| Vendor | Mechanism | Available |
|---|---|---|
| `claude` | `rate_limit_event` in `--output-format stream-json` | during a run |
| `codex` | `rate_limits` in `$CODEX_HOME/sessions/.../rollout-<thread_id>.jsonl` | any time |
| `agy` | `agy /usage`, `agy /credits` | any time |
| `copilot` | none known | — estimate only |

- A pool has **several simultaneous windows** (codex reports a 300-minute and a 10080-minute window).
  `.orc/ledger.json` stores per pool → `{windows: [{kind, used_fraction, resets_at}], observed_at,
  source, window_started, budget_units, spent_units, exhausted_until?}`.
- **Eligibility:** a pool is ineligible once **any** window reaches
  `used_fraction >= 1 - reserve_fraction`, until that window's `resets_at`.
  `reserve_fraction` is per-pool in `orc.toml`, default `0.15`, overridden by `--use-reserve`.
  The reserve is evaluated before a run starts and never re-asked mid-ladder, so an unattended run
  can neither block on a prompt nor spend the owner out of their own interactive CLI.
- **Fallback:** without telemetry, decrement abstract "run credits" per run — reported tokens if
  available, else a flat per-run estimate from config.
- On `rate_limited`: set `exhausted_until` to the next window boundary, reroute, print what happened.
  Telemetry is authoritative; a rate-limit error is a correction, not the primary signal.
- Windows reset: clear `spent_units` once `window_started` is older than the window length.
- `orc quota` prints pool, window, utilization, reset time, and **source** (telemetry or estimated).
  `orc quota set/reset <pool>` for manual correction. Label estimated numbers as estimated.

---

## 8. Verification harness

Order of truth for commands:

1. `AGENTS.md` / `CLAUDE.md` in the **target** repo (parse fenced commands under a "Commands"/"Testing" heading if present).
2. `orc.toml` `[verify]` overrides.
3. Auto-detection:

| Signal | Test | Lint/type | Build |
|---|---|---|---|
| `package.json` | `npm test` | `lint`/`typecheck` scripts if defined | `build` script if defined |
| `pyproject.toml` / `pytest.ini` | `pytest -q` | `ruff check .`; `mypy` if configured | — |
| `pom.xml` / `mvnw` | `./mvnw -q test` | — | `./mvnw -q clean verify` |
| `go.mod` | `go test ./...` | `go vet ./...` | `go build ./...` |
| `Cargo.toml` | `cargo test -q` | `cargo clippy -q` | `cargo build -q` |

- Run after every agent attempt, with timeout, cwd = repo, output captured to `.orc/runs/<id>/verify-N.txt`.
- Feed the **exact failing output** (tail-truncated per failure) into the next attempt's prompt. Agents never get to assert "tests should pass" — the harness decides.
- No tests detected → say so plainly, run lint/build only, and mark the run "verified: partial".

---

## 9. Review protocol (M3)

- **Reviewer selection:** first candidate in `lanes.standard`+`lanes.quality` whose vendor ≠ author vendor and whose pool has quota. A Copilot premium request (Sonnet @1x) is a legitimate budget reviewer.
- **Reviewer input:** task statement, final diff, verification results, target-repo `AGENTS.md` excerpt. **Never** the author transcript — fresh eyes are the point.
- **Output schema (force JSON, one reformat retry on parse failure):**

```json
[{"severity": "P0|P1|P2|P3", "file": "...", "line": 0, "issue": "...", "why": "...", "suggestion": "..."}]
```

- **Policy:** P0/P1 → one fix cycle by the standard lane (author model permitted) with findings + diff, then re-verify. P2/P3 → report only. Fixer disputes a P0/P1 → single consultant adjudication call (default `claude:opus-5@xhigh`) with both positions; verdict is final. One fix cycle max in MVP.

---

## 10. Safety rails (hard requirements)

- Refuse to start unless the target is a git repo; require clean or stashable state; create branch `orc/<slug>-<shortid>`; never touch the user's current branch.
- Never: force-push, delete branches, edit `.git`, weaken/disable/delete tests to make them pass, write outside the target repo.
  *Single carve-out (2026-09-07):* append `.orc/` to `.git/info/exclude`. That file is local-only and
  never committed; without it `.orc/` leaves the tree dirty so a second run refuses to start, and a
  supervised agent running `git add -A` can stage orc's own artifacts into the user's branch.
- An agent that modifies a test file aborts the run with a safety error. Enumerate test files via
  `git ls-files`, never a filesystem walk.
- Destructive-pattern denylist (e.g. `rm -rf` outside repo, `DROP TABLE`, schema migrations) → block unless `--allow-destructive`.
- Every run persists prompts, transcripts, diffs, and verification output under `.orc/runs/<id>/`.
- `fable_paid` runs require interactive confirmation showing an estimated cost; `--yes-paid` for scripted use.

---

## 11. CLI UX

```
orc "fix the failing auth test"                 # main flow
orc --lane quality "refactor the payment service"
orc --consult "should sessions live in redis or postgres?"   # single-shot consultant, no code changes
orc quota                                       # ledger table
orc log --last                                  # last run summary + paths
```

Activity trail (decisions/actions/results only — no chain-of-thought):

```
task a3f  branch orc/fix-auth-a3f
→ standard: codex terra@high        done 4m12s
→ verify: pytest                    2 failed
→ triage: lazy (tests never run) → terra@xhigh
→ verify: pytest                    34 passed · ruff ok
→ review: claude sonnet-5@high      1×P1 2×P3
→ fix P1: terra@high → verify ok
✓ 3 files changed (+118 −22) · est. usage: codex 3 runs, claude 1 run
```

---

## 12. Milestones (build strictly in order; stop at each acceptance gate)

### M1 — single-lane pipeline (prove the loop)
Claude Code adapter only. `orc "<task>"` → branch → agent (`claude:sonnet-5@high`) → verification harness → up to 2 retries with failure feedback (effort +1 on retry) → report + diff. Ledger records runs (stub budgets fine).

**Acceptance:**
- [ ] On `examples/sample-python` (seeded failing test), `orc "make the tests pass"` ends with the suite green, diff printed, transcript saved under `.orc/runs/`.
- [ ] On a repo with no tests, orc states that plainly and completes with lint only.
- [ ] With the `claude` CLI missing, orc exits with a clear one-line error, not a stack trace.
- [ ] Unit tests cover verify-command detection and the retry/feedback loop with a fake adapter.

### M2 — multi-vendor ladder, telemetry ledger
*Scope revised 2026-09-07 (rationale: `docs/superpowers/specs/2026-09-07-m2-design.md`).*
Codex **and Antigravity (`agy`)** adapters; lanes/ladder from `orc.toml`; lazy-vs-dumb triage;
telemetry-driven ledger with reserve policy; `orc quota`; `orc log`; structured failure feedback on
retries; run logging in `.orc/log.jsonl`; `volume` as a quota fallback.

`agy` was promoted from M4 because §14's headless question resolved affirmatively and it has the
cleanest quota probe of the three, which exercises `QuotaObservation` against real variety.

**Acceptance:**
- [ ] A mocked `rate_limited` result reroutes to the other vendor and marks the pool exhausted until reset.
- [ ] Triage rules covered by unit tests using synthetic `AgentResult`s (lazy → effort bump; dumb → rung change).
- [ ] `orc quota` shows every pool with utilization, reset time, and whether the number is observed or estimated.
- [ ] A pool past its reserve line is skipped before a run is spent, and `--use-reserve` overrides it.
- [ ] With every ladder rung reserve-blocked, the run degrades to the `volume` lane rather than failing.
- [ ] Structured failure context (exact error summary + touched file diff stat) is provided on verification failure retries.
- [ ] Exhaustion provides clear copy-pasteable terminal instructions to rescue the task branch interactively.
- [ ] Task runs are logged as JSONL in `.orc/log.jsonl` per §13, with `prompt_hash` and no prompt text.
- [ ] Each adapter has parser tests against recorded real CLI output, plus an opt-in `live` test that
      runs its actual argv against the installed CLI.
- [ ] `orc` runs against a target repository outside its own directory tree.

### M3 — cross-vendor review + consult mode
**Acceptance:**
- [ ] Review JSON parsed robustly (bad JSON → one reformat retry, then degrade to report-only).
- [ ] A P1 finding triggers exactly one fix cycle and a re-verify.
- [ ] `orc --consult` returns a single-shot answer, decrements the ledger, changes no files.
- [ ] Fable path is blocked without confirmation and prints an estimated cost when confirmed.

### M4 — Copilot adapter, polish
Discovery-first spike for the Copilot CLI (not installed as of 2026-09-07); wire the free lane;
graceful absence everywhere. *Antigravity moved to M2 — see §12 M2 and §14.*

### Non-goals for MVP (v2 parking lot — do not build)
Git worktrees & parallel subtasks · consensus mode · learned routing from logs · repo indexing/embeddings · browser verification · MCP transport · TUI/web UI · Windows support (macOS/Linux only).

---

## 13. Logging (feeds v2 learned routing)

One JSON line per task in `.orc/log.jsonl`:
`{task_id, prompt_hash, start_lane, attempts: [{vendor, model, effort, triage, verify, wall_s}],
reviewer, findings: {p0,p1,p2,p3}, outcome, wall_s, est_usage, quota: [{pool, window, used_fraction,
source}]}` — no code contents, no prompts. The full prompt is already persisted per §10 under
`.orc/runs/<id>/prompt-N.txt`, so `orc log` reads the run directory for human display and the JSONL
carries only the hash. Recording observed utilization per attempt is what makes the reserve default
calibratable from real data.

---

## 14. Open questions (resolve during build; none are blocking)

- ~~**Antigravity CLI headless capabilities**~~ — **resolved 2026-09-07.** `agy` is a separate product
  from Antigravity.app and is fully headless: `-p/--print`, `--model`, `--effort low|medium|high`,
  `--output-format text|json|stream-json`, `--json-schema`, `--print-timeout` (default 5m), and
  `agy /usage` for quota. Installed via the vendor script to `~/.local/bin/agy`. Flags are from docs
  and **must be re-verified against `agy --help`** before the adapter is written. Promoted to M2.
- **Copilot billing mode** — owner must check whether their plan is legacy premium-requests or the June-2026 credits model and set the pool budget accordingly. *(owner)*
- **Exact rate-limit error strings per CLI** — collect during adapter discovery; keep patterns in config. *(engineering)*
- **Weekly reset timestamps per subscription** — owner observes and sets in `orc.toml` during calibration week. *(owner)*

---

## 15. Kickoff instructions for the building agent

1. Read this file and `CLAUDE.md` end to end.
2. Scaffold per §4 (`uv init`, deps, package layout, `orc.toml` schema + example, `.gitignore` incl. `.orc/`).
3. Build **M1 only**, tests alongside code (harness + retry loop are unit-testable with fakes).
4. Stop at the M1 acceptance checklist and report: repo tree, test output, and a demo transcript of the sample-repo run.
