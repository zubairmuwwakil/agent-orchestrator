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
    antigravity.py  # M4
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

**Trade-offs accepted:** subprocess adapters are slower and lossier than SDKs but vendor-neutral and swappable; self-estimated quota is imprecise but the only option (no product exposes a usage API); Python over Go/TS for one-developer iteration speed.

---

## 5. Adapter contract

```python
class AgentAdapter(ABC):
    name: str  # "claude", "codex", ...

    def available(self) -> bool: ...  # CLI on PATH + authenticated
    def run(self, req: AgentRequest) -> AgentResult: ...


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
```

Rules for every adapter:

- **Discovery first.** The first implementation task per adapter is to run `<cli> --help` and read current official docs. Do not trust flag names from this spec — they drift. Known starting points only: `claude -p/--print` with JSON output, `--model`, `--effort`; `codex exec` for non-interactive runs; the Antigravity Go CLI (replaced Gemini CLI, June 2026 — capabilities unknown, treat as a spike); Copilot CLI (capabilities unknown, treat as a spike).
- Detect rate-limit/quota errors from exit codes + stderr patterns and return `rate_limited` so the ledger can mark the pool exhausted. Collect the real error strings during the discovery spike; keep the patterns in config.
- `consult` mode: one completion, no agentic tool loop. If a CLI cannot disable tools, constrain via prompt + lowest-permission flags, and cap `timeout_s` low.
- Missing/unauthenticated CLI → `available() == False`; router skips the lane and warns once per run. Never crash because a vendor is absent.
- Diffs come from `gitops` (git itself), never parsed from agent output.

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
attempts_per_rung = 2
max_total_attempts = 4
```

- Default start rung: `standard`. Overrides: `--lane volume|standard|quality`, `--agent vendor:model`, `--effort <level>`, `--no-review`.
- Within a lane, pick the first candidate whose pool is not exhausted (ledger) and whose adapter is available.
- **Triage after each failed verification (heuristics, no ML):**
  - *Lazy signals* → same model, effort +1: verify command never appeared in `ran_commands`; `tool_call_count` below a config floor; success claimed while verification fails.
  - *Dumb signals* → next rung: verification ran ≥2 times, files were edited, still failing.
  - Ambiguous → treat as lazy first (cheaper).
- Hard stop at `max_total_attempts`: surface best attempt, failing output, and transcripts to the human. Never loop forever.

---

## 7. Quota ledger

Reality: none of the four products exposes a clean usage API. The ledger is **self-estimated and advisory**, corrected by observed rate-limit errors.

- State in `.orc/ledger.json`: per pool → `{window: "5h"|"weekly"|"monthly", window_started, budget_units, spent_units, exhausted_until?}`. Units are abstract "run credits" configured per pool; adapter-reported tokens refine estimates when present.
- Decrement on every run: reported tokens if available, else a flat per-run estimate from config (per model tier, user-tunable).
- On `rate_limited`: set `exhausted_until` = next window boundary (5h boundary / configured weekly reset day / 1st of month), reroute to the next candidate, print what happened.
- `orc quota` prints a table (pool, window, est. remaining, exhausted-until). `orc quota set/reset <pool>` for manual correction.
- Print "estimated" wherever numbers appear. Calibration is a week-one user activity, not code.

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

### M2 — second vendor, ladder, real ledger
Codex adapter; lanes/ladder from `orc.toml`; lazy-vs-dumb triage; ledger with rate-limit detection; `orc quota`.

**Acceptance:**
- [ ] A mocked `rate_limited` result reroutes to the other vendor and marks the pool exhausted until reset.
- [ ] Triage rules covered by unit tests using synthetic `AgentResult`s (lazy → effort bump; dumb → rung change).
- [ ] `orc quota` shows both pools with estimated remaining.

### M3 — cross-vendor review + consult mode
**Acceptance:**
- [ ] Review JSON parsed robustly (bad JSON → one reformat retry, then degrade to report-only).
- [ ] A P1 finding triggers exactly one fix cycle and a re-verify.
- [ ] `orc --consult` returns a single-shot answer, decrements the ledger, changes no files.
- [ ] Fable path is blocked without confirmation and prints an estimated cost when confirmed.

### M4 — Antigravity + Copilot adapters, polish
Discovery-first spikes; wire `volume` and free lanes; graceful absence everywhere.

### Non-goals for MVP (v2 parking lot — do not build)
Git worktrees & parallel subtasks · consensus mode · learned routing from logs · repo indexing/embeddings · browser verification · MCP transport · TUI/web UI · Windows support (macOS/Linux only).

---

## 13. Logging (feeds v2 learned routing)

One JSON line per task in `.orc/log.jsonl`:
`{task_id, prompt_hash, start_lane, attempts: [{vendor, model, effort, triage, verify}], reviewer, findings: {p0,p1,p2,p3}, outcome, wall_s, est_usage}` — no code contents, no prompts.

---

## 14. Open questions (resolve during build; none are blocking)

- **Antigravity CLI headless capabilities** — unknown. M4 spike; if it can't run non-interactively, drop the lane and note it. *(engineering)*
- **Copilot billing mode** — owner must check whether their plan is legacy premium-requests or the June-2026 credits model and set the pool budget accordingly. *(owner)*
- **Exact rate-limit error strings per CLI** — collect during adapter discovery; keep patterns in config. *(engineering)*
- **Weekly reset timestamps per subscription** — owner observes and sets in `orc.toml` during calibration week. *(owner)*

---

## 15. Kickoff instructions for the building agent

1. Read this file and `CLAUDE.md` end to end.
2. Scaffold per §4 (`uv init`, deps, package layout, `orc.toml` schema + example, `.gitignore` incl. `.orc/`).
3. Build **M1 only**, tests alongside code (harness + retry loop are unit-testable with fakes).
4. Stop at the M1 acceptance checklist and report: repo tree, test output, and a demo transcript of the sample-repo run.
