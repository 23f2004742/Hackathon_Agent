# Reference Decisions — Hackathon Agent OS build

Prior work that materially shaped this system. Recorded per the cross-hackathon
reuse rule: read → learn → adapt → copy safe code only. Neither source project
was modified.

---

## Claude agent scaffold — `tool_runner` loop and tool contract

- **Source:** `claude-agent-scaffold`, archived at `~/Desktop/hackathon.zip`
  (indexed in `.knowledge/index.json`)
- **Used for:** `hackathon_os/llm.py::AnthropicBackend` and the whole shape of
  `hackathon_os/tools/base.py`
- **What was reused:** the `client.beta.messages.tool_runner` loop with history
  mirroring, `pause_turn` restarts, refusal-fallback opt-in and typed error
  handling; the `@beta_tool` "docstring is the prompt" contract; workspace path
  resolution with `../` rejection; 30k tool-result truncation; env-var
  substitution so secrets stay out of the transcript.
- **Changes:** the scaffold has one global workspace and one flat tool list.
  This system needs per-agent boundaries, so `WORKSPACE`/`AUTO_APPROVE` globals
  became a thread-local `ExecutionContext` carrying a tool allowlist and a write
  scope; `configure()` became `using(ctx)`. Tools now go through `guard()` and
  `resolve_for_write()`. Added a registry so a spec can name tools as strings.
- **Verified:** the SDK's `BetaFunctionTool` is directly callable and exposes
  `.func`, `.name` and `.input_schema` — confirmed before building on it, which
  is what let one decorator serve both the API and direct unit testing.
- **Status:** Integrated

---

## patterns.md — "tools return error strings, never raise"

- **Source:** `.knowledge/patterns.md`, from `claude-agent-scaffold` (HIGH)
- **Used for:** every tool in `hackathon_os/tools/`
- **Changes:** made structural — a shared `fail(e)` helper, and every tool body
  wrapped in `try/except Exception`.
- **Status:** Integrated · guarded by `test_tools_return_errors_rather_than_raising`

---

## patterns.md — "optimise the metric you are scored on"

- **Source:** `.knowledge/patterns.md`, from `playhack-iit-guwahati` (HIGH).
  PLAYHACK's F1-optimal threshold scored 0.2141; the composite-optimal one
  scored 0.4214.
- **Used for:** the ML Engineer's mission, as its first numbered rule
- **Changes:** generalised from "threshold selection" to "match the objective to
  the scoring rule, especially when the rule couples several outputs", and
  paired with a `knowledge_search` instruction so the agent can read the
  original evidence.
- **Status:** Integrated

---

## patterns.md — "publish trivial baselines beside your score"

- **Source:** `.knowledge/patterns.md`, from `playhack-iit-guwahati` (HIGH)
- **Used for:** the Presentation Builder's results-slide instruction, and the
  slide template the simulated backend emits
- **Changes:** stated as a deck rule ("a score with nothing to compare it to
  tells a judge nothing") rather than an evaluation-methodology rule.
- **Status:** Integrated

---

## patterns.md — "name your assumptions"

- **Source:** `.knowledge/patterns.md`, from `playhack-iit-guwahati` (MEDIUM).
  PLAYHACK stated plainly that its composite weighting was its own and not the
  official formula.
- **Used for:** `assumptions` as a first-class field in the handoff protocol
- **Changes:** promoted from a documentation habit to a schema field, so the
  Final Auditor can read every agent's assumptions mechanically rather than
  hoping they were written down.
- **Status:** Integrated

---

## Not reused, and why

- **PLAYHACK's LightGBM pipeline** (`code/playhack.py`) — evaluated, rejected.
  It is a tabular-ML pipeline; this build is agent infrastructure with no
  modelling component. Its *methodology* transferred; its code did not.
- **PLAYHACK's presentation structure** — deferred. It is indexed for a future
  hackathon's Presentation Builder to draw on, but this build produces no pitch
  of its own.
