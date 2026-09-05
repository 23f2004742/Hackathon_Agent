# Cross-Hackathon Knowledge Index

Shared, persistent memory across every hackathon in this directory.
Machine-readable twin: `index.json`. Lessons: `patterns.md`.

**Last updated:** 2026-09-04 (Agent OS build)

---

## Inventory

### 0. Hackathon Agent OS
`slug: hackathon-agent-os` · **in-tree** · `hackathon_os/`

| Field | Value |
|---|---|
| Problem domain | Multi-agent orchestration for hackathon delivery |
| Technology | Python 3.13, anthropic SDK, python-pptx, reportlab, pytest |
| AI/ML usage | 28 specialist agents over `tool_runner`; per-agent model/effort selection |
| Architecture | Orchestrator + dependency task graph + thread-local `ExecutionContext` enforcing tool allowlist and write scope; typed handoffs; dual real/simulated backend |
| Features | Negation-aware agent selection · artifact contracts · 65 postconditions · parallel waves · self-correction · provenance ledger · real pptx/pdf/zip · 3 memory layers |
| Outcome | 63 passing tests; 27-task synthetic hackathon completes in 7 waves, 37 files, real 7-slide pptx, secret-free zip. **Not validated against a live model.** |
| Selection status | n/a — infrastructure |
| Reusable | `ExecutionContext` boundary pattern · handoff validator · task graph · security scanners · glyph fallback · simulated-backend pattern |

**Why it matters:** the `ExecutionContext` idea — enforce agent boundaries in
code rather than in prompts — is the transferable one. Any future multi-agent
build should start from `tools/base.py` and `handoff.py` rather than from a
prompt template.

---

### 1. PLAYHACK — ML Track, IIT Guwahati (Round 1)
`slug: playhack-iit-guwahati` · **external** · `~/Desktop/iit-g hacathon` · [repo](https://github.com/23f2004742/playhack-ml-track-iit-guwahati)

| Field | Value |
|---|---|
| Problem domain | Sports-science injury prediction (3 linked tasks) |
| Technology | Python, LightGBM, scikit-learn, pandas, joblib, Jupyter |
| AI/ML usage | LGBM classifier (injury), LGBM L1 regressor (onset), per-sport median + correction (recovery) |
| Architecture | Offline batch ML pipeline; notebook as source of truth → `playhack.py` + `predict.py`; models serialized to `models/` |
| Features | 30-day window features w/ structural leakage guard · composite-objective threshold selection · 3-seed OOF eval · threshold sweep vs trivial baselines · label-shuffle + adversarial validation |
| Outcome | F1 0.5194 · recall 0.9994 · onset MAE 2.683 (skill 0.648) · recovery MAE 2.944 (skill 0.092) · self-estimated composite 0.4196 ± 0.0032 |
| Selection status | Unknown — not recorded |
| Reusable | CV/multi-seed harness · train/serve split · threshold-sweep method · pitch structure |

**Why it matters:** the strongest asset here is *methodology*, not the model. The
leakage proof, the baseline-reference table and the honest reporting of a
within-noise margin are transferable to any scored/leaderboard hackathon.

---

### 2. Claude tool-calling agent scaffold
`slug: claude-agent-scaffold` · **external, archived** · `~/Desktop/hackathon.zip`

| Field | Value |
|---|---|
| Problem domain | Agentic AI / developer tooling |
| Technology | Python, `anthropic>=1.3.0`, requests, `claude-opus-5` |
| AI/ML usage | `client.beta.messages.tool_runner` loop · `@beta_tool` schema generation · Anthropic server-side tools |
| Architecture | Single-process CLI agent; `tools.py` = tool surface, `agent.py` = loop + resilience |
| Features | shell/read/write/edit/list/http tools · workspace sandbox · approval prompts (`--yolo`) · 30k result truncation · env-var secret substitution · refusal fallback |
| Outcome | Working scaffold, unattached to a submission |
| Reusable | **Whole thing.** Fastest path to a demoable agent for any agentic-AI hackathon |

**Secret scan:** clean — `.env.example` carries a placeholder key only.

> ⚠️ This was the previous contents of `hackathon/` itself, archived to a zip
> on 2026-09-04. It has **not** been extracted back into the tree. See
> "Open questions" below.

---

## Relevance quick-reference

When a new problem statement arrives, start here:

| If the new problem involves… | Look at |
|---|---|
| Leaderboard / scored ML submission | **playhack** — HIGH |
| Tabular, time-window, or forecasting data | **playhack** — HIGH |
| Any metric you must optimize a threshold against | **playhack** — HIGH |
| Agents, tool-calling, LLM orchestration | **claude-agent-scaffold** — HIGH |
| Needs a shell/file/HTTP-capable AI worker | **claude-agent-scaffold** — HIGH |
| Agents, orchestration, multi-agent systems | **hackathon-agent-os** — HIGH |
| Any hackathon at all (it runs the whole pipeline) | **hackathon-agent-os** — HIGH |
| Web app / dashboard / frontend UI | *nothing production-grade yet* |
| Computer vision | *nothing yet* |
| Pitch-only or business-track | **playhack** presentation — MEDIUM |

---

## Coverage gaps

Honest accounting of what this knowledge base cannot yet help with:

- **No frontend/UI work of any kind.** No React, no dashboard, no component library.
- **No backend/API service.** No FastAPI/Flask server, no auth, no DB schema.
- **No deployment configuration.** Nothing containerized or hosted.
- **No judge feedback captured.** Neither project records how it was received,
  so `PRESENTATION_LESSONS` and `SUBMISSION_LESSONS` are inference, not evidence.
- **No post-mortems from the two earlier projects.** Both were reconstructed by
  inspection after the fact. `hackathon-agent-os` has `BUILD_REPORT.md`, which
  logs its failures as they happened.
- **The Agent OS has never run against a live model.** Its machinery is tested;
  the quality of what Claude produces through it is unknown.

---

## Open questions for the operator

1. **Restore the scaffold?** `hackathon.zip` holds the old `hackathon/` contents.
   Extract it to `.knowledge/components/claude-agent-scaffold/` as a vetted
   component library, or leave it archived?
2. **Relocate PLAYHACK?** It sits outside the tree as `iit-g hacathon`. Moving it
   to `hackathon/hackathon_01/` would consolidate the library — but that
   modifies an old project, so it needs an explicit instruction.

---

## Maintenance contract

- Update this file **and** `index.json` when a hackathon completes.
- Write `<hackathon>/AGENT/postmortem.md` at the end of every event.
- Promote durable lessons into `patterns.md` with a source and a confidence level.
- Record borrowed work in `<hackathon>/AGENT/reference_decisions.md`.
- Never copy secrets, `.env` files, datasets or credentials between projects.
