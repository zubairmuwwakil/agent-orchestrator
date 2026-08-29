---
reviewDate: 2026-11-30
---

# AI routing fleet

This is the public roster for choosing a model, effort, and routing role. Operational
budgets, reset windows, billing details, and time-limited credits belong in `orc.toml`
or gitignored local configuration, never here.

## Pools

| Pool id | Models and effort levels | Routing role |
|---|---|---|
| `claude_pro` | Sonnet 5, Opus 5, Opus 4.8/4.7/4.6, Haiku 4.5 — low, medium, high, xhigh; max/ultracode when available | Quality lane; Opus 5 is the strongest included consultant |
| `codex_plus` | GPT-5.6 Sol, Terra, Luna — light, medium, high, xhigh; ultra on Sol and Terra | Luna for volume, Terra for standard work, Sol for escalation |
| `antigravity_gemini` | Gemini 3.7 Flash, Gemini 3.1 Pro — low, medium, high | Flash is the default low-cost agent lane |
| `antigravity_claude` | Claude Sonnet 4.6, Opus 4.6, GPT-OSS 120B | Reserve for browser-verified flows; exclude from the default ladder |
| `copilot` | Base models; Haiku, Sonnet, Opus, GPT-5.6 family | Default agent and budget reviewer lanes; do not use Copilot's built-in code-review mode |
| `fable_paid` | Fable 5 | Confirmation-gated consultant of last resort |

## Routing rules

1. Route `(model, effort)`, not just model. Increase effort on the same model before
   escalating to another rung.
2. Triage failure as lazy or dumb. Skipped verification, few tool calls, or unsupported
   success claims are lazy: retry the same model at higher effort. Genuine iteration
   that still fails is dumb: move to the next rung. Treat ambiguity as lazy first.
3. Expensive models are consultants, not open-ended agents. Opus 5 at xhigh or above,
   GPT-5.6 Sol at xhigh/ultra, and Fable 5 receive a prepared context package for a
   single-shot consultation.
4. Review with fresh context from a different vendor than the author. Give the reviewer
   the task, diff, and verification results, never the author's transcript.
