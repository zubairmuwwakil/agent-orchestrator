# M3 brainstorm — session handoff

M2 is complete and its acceptance criteria are ticked in `SPEC.md` §12. This hands the M3
design conversation to a fresh session so it does not have to re-derive M2's context.

**M3 scope per `SPEC.md` §9 and §12:** cross-vendor review with fresh context, a fix cycle
for P0/P1 findings, consultant adjudication of disputes, `orc --consult` single-shot mode,
and the confirmation-gated Fable path.

## Launch

```bash
claude --model opus --effort high
```

Brainstorming is high-judgment and low-volume — a design conversation, not a fifteen-task
build — so Opus costs little here and this is the milestone with the most design surface
left. Run `orc quota` first: the tool now tells you whether that lane is affordable, and if
`claude` is near its reserve, `codex --model gpt-5.6-sol -c model_reasoning_effort="high"`
is the equivalent quality lane in the other vendor.

## What M2 delivered, so M3 can assume it

- Three working adapters — `claude`, `codex`, `antigravity` (`agy`) — each with fixture
  parser tests and an opt-in `live` argv test.
- `QuotaObservation` on `AgentResult` plus `AgentAdapter.quota_probe()`; `ledger.py` is
  vendor-agnostic and applies a per-pool `reserve_fraction` (default 0.15) before a run is
  spent. `--use-reserve` overrides.
- Ladder walk with lazy-vs-dumb triage, `volume` as a quota fallback beside the ladder.
- `orc "<task>"`, `orc quota`, `orc log`; flags `--lane`, `--agent`, `--effort`,
  `--use-reserve`, `--target`, `--allow-destructive`.
- Git safety: `.orc/` in `.git/info/exclude`, diffs against the run's base commit, test-file
  enumeration that is deliberately ignore-unaware.
- `.orc/log.jsonl` per §13 — `prompt_hash`, never prompt text. `reviewer` and `findings` are
  already in the schema as nulls, waiting for M3 to populate them.

## Verified capabilities that de-risk M3

Checked against the installed binaries on 2026-09-08, from `--help`. **Not yet exercised
end to end** — confirm each with a real call before designing around it.

| Need | claude | codex | agy |
|---|---|---|---|
| Forced JSON findings (§9) | `--json-schema <schema>` | `--output-schema <FILE>` | `--json-schema` (final result only) |
| Consult mode, tools off (§5) | `--tools ""` disables all tools | `-s read-only` | `--sandbox`, `--mode plan` |
| Cost ceiling (§10) | `--max-budget-usd <amount>` | — | — |

Three consequences worth carrying into the conversation:

1. **All three vendors can be forced to emit schema-valid JSON.** `SPEC.md` §9's "force
   JSON, one reformat retry on parse failure" was written assuming prompt-and-hope. Native
   schema enforcement is a stronger mechanism, and the reformat retry may become a fallback
   rather than the primary path.
2. **Fable needs no new adapter.** `claude --model fable` (or the full `claude-fable-5`) is
   accepted by the existing CLI, so `fable_paid` is the `ClaudeCodeAdapter` with a different
   model plus a cost gate — not a fourth adapter. `--max-budget-usd` gives an enforceable
   ceiling on top of §10's confirmation, and the `result` event already carries
   `total_cost_usd`, which M2's parser reads.
3. **Consult mode differs per vendor.** Only claude can truly disable tools. codex offers a
   read-only sandbox and agy a plan mode — both prevent writes but still allow a tool loop.
   §5 anticipated this ("if a CLI cannot disable tools, constrain via prompt and lowest
   permission flags, and cap `timeout_s` low"), so the design just needs to say what
   "consult" guarantees when the guarantee is weaker.

**Watch out:** `codex` rejects `-s` together with `--approve-for-me`. M2's agent path uses
`--approve-for-me`; a read-only consult path must use `-s read-only` and drop it. That exact
pair once shipped green and broke every codex run.

## Questions the brainstorm should resolve

Ordered by what other decisions depend on. Recommendations are starting positions, not
conclusions.

1. **Native schema enforcement, or prompt-and-parse?**
   *Recommendation:* native per-vendor schema, with the reformat retry kept as the fallback
   for a vendor whose enforcement proves unreliable. Verify with one real call per vendor
   before committing — that is the same gate that caught the codex defect.
2. **Does reviewer selection respect the reserve?** §9 picks the first candidate whose vendor
   differs from the author. M2 added eligibility that §9 predates.
   *Recommendation:* yes — a review is a spend like any other, and being unable to review
   should degrade to report-only rather than block the run.
3. **Always review, or scale to blast radius?** §9 implies always; `FLEET.md` rule 4 says
   scale to blast radius and skip where an automated check is the better reviewer. These
   disagree.
   *Recommendation:* resolve explicitly in favour of FLEET rule 4 and amend §9, since M2's
   own experience — a `live` test catching what a reviewer would have argued about — is
   evidence for it.
4. **What does `consult` guarantee across vendors?** See the asymmetry above.
   *Recommendation:* define consult as "no repository writes", which all three can enforce,
   rather than "no tool loop", which only claude can.
5. **Is Fable a pool, a candidate, or a mode?**
   *Recommendation:* a candidate in `lanes.consultant` on the existing claude adapter, with
   its own pool for accounting and a confirmation gate. Owner question: whether
   `claude --model fable` bills the subscription or pay-per-token credits — `SPEC.md` §2.10
   says pay-per-token, so confirm before the gate's cost estimate is designed.
6. **Who adjudicates a disputed P0/P1, and is one cycle still right?** §9 says a single
   consultant call, verdict final, one fix cycle max.
   *Recommendation:* keep both limits; they are what stop a review loop from becoming the
   expensive part of a run.
7. **Do the native review commands play any part?** `codex review` and `codex exec review`
   exist and are non-interactive. `FLEET.md` bars Copilot's built-in review at 13x, but says
   nothing about codex's.
   *Recommendation:* ignore them for M3. §9 needs the reviewer to see a specific context
   package and return a specific schema; a vendor's own review mode controls neither.

## Kickoff prompt

```
Read AGENTS.md, then SPEC.md in full — §9 (review protocol), §5 (adapter
contract) and §12 (milestones) matter most. Then read
docs/superpowers/plans/2026-09-08-m3-brainstorm-handoff.md, which summarizes
what M2 delivered and lists verified CLI capabilities that change M3's shape.

We are designing M3: cross-vendor review, the P0/P1 fix cycle, consultant
adjudication, `orc --consult`, and the confirmation-gated Fable path. Do not
write implementation code — this session ends at an approved design.

Use the superpowers:brainstorming skill. Treat this as architectural: ask
clarifying questions one at a time, and for each one give your recommended
answer. Where a question can be answered by reading the codebase or by running
a CLI's --help, do that instead of asking me.

Do not trust the flag tables in the handoff. They came from --help and have not
been exercised end to end. Confirm anything you design around with a real call;
a plausible flag combination taken on faith once broke every codex run in this
repo and shipped with a green test suite.

Three of the questions require amending SPEC.md, which AGENTS.md makes the
source of truth: whether review scales to blast radius (§9 and FLEET rule 4
currently disagree), whether the reviewer respects the quota reserve M2 added
after §9 was written, and what `consult` guarantees on vendors that cannot
disable tools. Flag those rather than deciding them silently.

Finish by writing the design to docs/superpowers/specs/YYYY-MM-DD-m3-design.md,
amending SPEC.md, and committing. Then stop for review before any plan is
written.
```

## Done when

A committed M3 design document, the `SPEC.md` §9/§12 amendments it implies, and the three
conflicts above resolved explicitly rather than by silent choice. Implementation planning is
a separate session.
