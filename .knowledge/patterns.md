# Cross-Hackathon Patterns

Durable lessons distilled from projects in `index.md`. Each carries a **source**
and a **confidence**. These are evidence, not rules — the current hackathon's
brief always overrides anything here.

**Confidence scale**
- `HIGH` — demonstrated in a project *and* the reasoning generalizes
- `MEDIUM` — demonstrated once; plausible but not yet re-tested
- `LOW` — inferred by inspection, never validated against an outcome

> ⚠️ **Read this first.** These entries derive from **three** projects, none of
> which recorded judge feedback or a final placement. Nothing here has been
> confirmed against a competition *result*. Treat `PRESENTATION_LESSONS` and
> `SUBMISSION_LESSONS` especially as reasoned priors, not proven tactics.
>
> The `hackathon-agent-os` entries are the exception in kind: they were
> confirmed by tests and by a run that failed, not by a judge. They are HIGH
> because something broke and the fix was verified — not because anyone won.

**Corpus:** 3 projects · 0 with known placement · 0 with recorded judge feedback

---

## SUCCESSFUL_PATTERNS

**Enforce agent boundaries in code, never in the prompt.**
A specialist is only real if the runtime stops it doing what it should not: a
tool allowlist (the model is never sent the tool), a write scope (out-of-scope
writes return an error naming the real scope), and an artifact contract (a
"completed" claim with no files on disk is downgraded to FAILED). Prompt-level
role separation degrades silently; these do not.
*Source: hackathon-agent-os · Confidence: HIGH*

**Separate the agent that tests from the agent that fixes.**
The Tester has no write access to `src/`. An agent that can patch the code it
is testing will make the report green rather than make the product work.
Failures leave as proposed tasks for a different specialist.
*Source: hackathon-agent-os · Confidence: HIGH*

**Build a deterministic backend that drives the real tool layer.**
A simulated backend that executes the same tools with no API makes a
multi-agent system testable end to end, for free, in seconds. It proves
boundaries, contracts, scheduling and packaging; it cannot prove content
quality, and every artifact it writes should say so.
*Source: hackathon-agent-os · Confidence: HIGH*

**Run the end-to-end test before believing the design.**
The synthetic hackathon exposed seven defects that reading the code did not,
including results being filed under the wrong task in parallel waves. A green
run hid it; only a failure would have revealed it in production.
*Source: hackathon-agent-os · Confidence: HIGH*

**Optimize against the actual scoring rule, not the per-task metric.**
PLAYHACK's decision threshold was swept against the composite objective, not F1.
The F1-optimal threshold (0.480) scored 0.2141; the composite-optimal one (0.012)
scored 0.4214 — nearly 2x better on the metric that counts. The tasks were
coupled: a missed injury was charged MAE=30 on both timing targets, so onset
skill only turned positive above recall 0.819.
*Source: playhack-iit-guwahati · Confidence: HIGH*

**Publish trivial baselines next to your score.**
PLAYHACK reported predict-nobody (0.0000), predict-everyone-with-constant-timing
(0.1731) and predict-everyone-with-our-timing (0.4210) beside its own 0.4214. A
judge cannot size a score in a vacuum; the table does that work for them.
*Source: playhack-iit-guwahati · Confidence: HIGH*

**Prevent leakage structurally, then prove it empirically.**
Truncation happened once, inside `load_observation_window()`, so future days
physically could not reach a feature. Section 5 then rebuilt features from files
with the risk window deleted and confirmed identical output. Design plus proof
beats either alone.
*Source: playhack-iit-guwahati · Confidence: HIGH*

**Report multi-seed means with a spread.**
Three seeds (42/7/202) gave 0.4196 +/- 0.0032 — which is what let the team
*notice* their margin over a trivial baseline was inside noise. Single-seed
numbers hide exactly this.
*Source: playhack-iit-guwahati · Confidence: HIGH*

**Let the docstring be the prompt.**
The `@beta_tool` contract derives the JSON schema from type hints and the
docstring, so tool-description quality directly drives tool-selection quality.
One documented function = one working tool.
*Source: claude-agent-scaffold · Confidence: MEDIUM*

**Return errors as strings from agent tools; never raise.**
A raised exception kills the agent loop. An error string lets the model read what
went wrong and retry. Cheap to implement, large robustness gain in a live demo.
*Source: claude-agent-scaffold · Confidence: HIGH*

---

## FAILED_PATTERNS

*Growing. `hackathon-agent-os` logged its dead ends as it hit them, which is
why its entries below are specific. The two earlier projects did not, and their
sections remain thin as a result — log failures while they hurt, not later.*

**Keyword capability detection that ignores negation.**
"No interface required" selected the whole frontend team; "no backend, no
database" selected both. Match a keyword, then check the clause around it for a
negator before acting on it.
*Source: hackathon-agent-os · Confidence: HIGH*

**Pairing parallel results by list position.**
Work regrouped for scheduling comes back in a different order. Zipping results
against the original list files each agent's output under a neighbour's task —
invisible while everything passes, corrupting once anything fails. Pair at
dispatch.
*Source: hackathon-agent-os · Confidence: HIGH*

**Unicode box-drawing in a CLI without a fallback.**
`✓ ○ ━ █` raise UnicodeEncodeError on a cp1252 Windows console and take the
whole command down. Probe stdout, upgrade to UTF-8 where possible, fall back to
ASCII where not.
*Source: hackathon-agent-os · Confidence: HIGH*

**Over-broad secret globs that strip useful files.**
A `.env.*` exclusion also removes `.env.example`, which is exactly the file a
judge needs to configure the project. Exclude secrets; whitelist templates.
*Source: hackathon-agent-os · Confidence: MEDIUM*

**Chasing the headline metric in isolation (near-miss).**
Tuning for F1 would have roughly halved PLAYHACK's composite. Caught before
submission, but only because someone swept the composite explicitly.
*Source: playhack-iit-guwahati · Confidence: MEDIUM*

**Trusting in-sample performance.**
PLAYHACK recorded a "substantial" in-sample vs out-of-fold gap and had to base
all model selection on OOF. Assume this gap exists until measured.
*Source: playhack-iit-guwahati · Confidence: HIGH*

**Over-trusting an ID feature.**
`team_id` looked informative but carried nothing beyond `sport` — 30 teams sat 5
per sport. Check whether a categorical is just a proxy for one you already have.
*Source: playhack-iit-guwahati · Confidence: MEDIUM*

---

## REUSABLE_COMPONENTS

| Component | Source | Use for |
|---|---|---|
| `code/playhack.py` | playhack | Windowed feature build, CV harness, multi-seed loop |
| `code/predict.py` | playhack | Load-model-and-score inference; clean train/serve split |
| `results/threshold_sweep.csv` | playhack | Template for sweeping a threshold against a composite metric |
| `PLAYHACK_Presentation.pptx` | playhack | Pitch skeleton: EDA to methodology to validation to results |
| `tools.py` | agent-scaffold | Drop-in agent tool surface (shell/file/http) with sandboxing |
| `agent.py` | agent-scaffold | Agent loop: history mirroring, pause_turn restarts, typed errors |
| `hackathon_os/tools/base.py` | agent-os | **ExecutionContext**: thread-local tool allowlist + write scoping |
| `hackathon_os/handoff.py` | agent-os | Typed handoff + validator that downgrades unearned success |
| `hackathon_os/taskgraph.py` | agent-os | Dependency graph: cycles, priority, blocked propagation |
| `hackathon_os/tools/security.py` | agent-os | Secret scanners that separate placeholders from leaks |
| `hackathon_os/glyphs.py` | agent-os | Terminal capability detection with ASCII fallback |
| `hackathon_os/llm.py` | agent-os | Real + simulated backend pair behind one interface |

**Not yet available:** production frontend components, a real API server, auth,
DB schema, deployment config. Build fresh and index them.

---

## TECHNICAL_LESSONS

- **Match the loss to the metric.** MAE metric means an L1 objective, because the
  conditional median is the correct target. *(playhack · HIGH)*
- **Try the strong-prior hybrid first.** Recovery duration was ~0.7 x per-sport
  median + 0.3 x learned correction, because recovery was nearly a per-sport
  constant. A constant plus a small correction often beats a model that has to
  rediscover the constant. *(playhack · MEDIUM)*
- **Run a label-shuffle test.** Shuffled labels scoring ~0.50 against a real 0.75
  is cheap, decisive evidence that signal comes from features, not a leak.
  *(playhack · HIGH)*
- **Run adversarial validation** to confirm train and test are drawn from the
  same population before trusting your CV. *(playhack · MEDIUM)*
- **Sandbox agent file paths.** Resolve every path inside the workspace and
  reject `../`. *(agent-scaffold · HIGH)*
- **Truncate tool results** (~30k chars) or one `cat` of a large file destroys
  the context window mid-demo. *(agent-scaffold · HIGH)*
- **Substitute secrets locally**, never through the model — pass `"$MY_TOKEN"`
  and resolve it client-side so it stays out of the transcript.
  *(agent-scaffold · HIGH)*
- **Normalise a path root once, at the boundary.** A relative root compares
  false against every resolved path and rejects the entire project. Resolve it
  in `__post_init__` rather than trusting callers. *(agent-os · HIGH)*
- **Serialise agents that share a write target.** Four researchers appending to
  one ledger concurrently is a lost-update bug. Group by write-scope overlap.
  *(agent-os · HIGH)*
- **Keep one source of truth.** PLAYHACK's notebook generated the scripts;
  reported figures and submitted predictions came from the same Section 8 code
  path, so the numbers could not drift apart. *(playhack · HIGH)*

---

## PRESENTATION_LESSONS

*No judge feedback was recorded for either project. Everything here is inferred
from artifacts — treat as LOW unless noted.*

- **Name your assumptions.** PLAYHACK stated plainly that its composite weighting
  was its own and not the official formula. Being explicit about what you do not
  know reads as competence, not weakness. *(playhack · MEDIUM)*
- **Disclose a within-noise margin.** The team reported that their threshold beat
  predict-everyone by 0.0004 against +/-0.0032 seed noise, and explained why they
  kept it anyway. A judge who finds this themselves is far more damaging than one
  who is told. *(playhack · MEDIUM)*
- **Explain surprising output.** A 99.7% positive rate looks broken until it is
  shown to follow from the scoring rule. Pre-empt the obvious objection.
  *(playhack · MEDIUM)*
- **Structure:** EDA, then methodology, then evaluation, then results.
  *(playhack · LOW)*

---

## RESEARCH_LESSONS

- **Read the scoring rules as an optimization problem before modeling.** The
  recall thresholds where each skill score turns positive (0.819 onset, 0.989
  recovery) were derivable from the rules on paper and drove the entire modeling
  strategy. *(playhack · HIGH)*
- **Probe the data's structure before feature engineering.** Discovering that
  teams sat 5-per-sport, and that recovery was near-constant per sport, shaped
  the model more than any tuning did. *(playhack · MEDIUM)*

---

## SUBMISSION_LESSONS

- **Ship models + config + an inference script**, not just predictions. PLAYHACK
  serialized fitted models and the config needed to apply them, so results were
  reproducible by a third party. *(playhack · HIGH)*
- **Include a plain-text README** with results, method, validation and repro
  steps, so nothing depends on the reviewer opening a notebook.
  *(playhack · MEDIUM)*
- **State provenance of every number** — "out of fold, averaged over 3 seeds, no
  test labels used at any point" — and make it verifiably true.
  *(playhack · HIGH)*
- **Note the environment** (Python >=3.10 plus an exact library list) so a
  reviewer can actually rerun it. *(playhack · MEDIUM)*

---

## HOW TO EXTEND THIS FILE

At the end of each hackathon:
1. Write `<hackathon>/AGENT/postmortem.md` — what worked, what failed, what you
   would do differently, and any judge feedback **verbatim**.
2. Promote durable lessons here with source + confidence.
3. Raise confidence when a lesson holds in a second project; delete it when it
   fails. A lesson that never changes level is a lesson nobody re-tested.
4. Record what you actually borrowed in `<hackathon>/AGENT/reference_decisions.md`.
