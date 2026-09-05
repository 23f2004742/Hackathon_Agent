# BUILD_REPORT.md

**Built:** 2026-09-04 · **Upgraded:** 2026-09-05 · **Status:** working, with one
stated limitation

## What was built

| | |
|---|---|
| Specialists | 28 across 7 teams |
| Tools | 27 local + 1 Anthropic-hosted (`web_search`), 13 categories |
| Postconditions | 66 declarative checks across 32 distinct artifacts |
| Code | 10,190 implementation + 3,062 test lines across 42 modules |
| Tests | **240 passing** |
| CLI | 26 commands |

```
hackathon_os/
├── orchestrator.py      the unified planner, waves, self-correction, replanning
├── planner.py           two-stage specialist selection (rules + Claude planner)
├── model_planner.py     per-task model choice, default-first, escalation record
├── token_optimizer.py   context prioritisation, dedup, compression, budgets
├── packaging.py         submission package + secret scanning
├── github.py            init / prepare / push, confirmation-gated
├── taskgraph.py         dependency graph, priority, blocked propagation
├── handoff.py           AgentResult + the contract validator
├── state.py             ProjectState, persisted and resumable
├── context.py           three-layer targeted retrieval
├── ledger.py            fingerprinted replay of completed work
├── routing.py           effort/turn tiers per role
├── auth.py              subscription-only credential policy
├── llm.py               Backend, AnthropicBackend, SimulatedBackend
├── subscription.py      the Claude Agent SDK backend
├── simulation.py        contract-satisfying artifact synthesis
├── dashboard.py         status rendering
├── glyphs.py            terminal capability detection + ASCII fallback
├── cli.py               the command surface
├── agents/              base.py + 7 team spec modules
└── tools/               base, filesystem, shell, research, project,
                         documents, security, handoff_tool
```

## The 2026-09-05 upgrade

Four systems added on top of the existing architecture. Nothing was rebuilt: the
task graph, ledger, handoff protocol, access boundaries and 28 specialists are
unchanged, and all 100 pre-existing tests still pass.

**1. Token optimizer** (`token_optimizer.py`). Candidate context is prioritised
into seven bands, deduplicated across slices, compressed by *keeping* decisions,
interfaces, endpoints, constraints, paths, errors and acceptance criteria rather
than truncating, and trimmed to a per-task budget sized from the task's own
effort, impact and priority. Metrics are persisted and reported by `status`.

**2. Intelligent specialist selection** (`planner.py`). The keyword pass is now
stage one of two; stage two asks Claude for a structured staffing plan over the
brief and roster alone. The merge keeps the guardrails in code — the planner may
not remove the mandatory delivery spine, may not overrule a capability the brief
states outright, and may not invent a specialist. Both the inclusions and the
exclusions carry specific reasons and are persisted.

**3. GitHub packaging** (`packaging.py`, `github.py`). `package` builds
`dist/submission/`; `github init|prepare|push` prepares a repository and gates
the one outward-facing action behind an explicit confirmation and a passing
secret scan.

**4. Dynamic model planner** (`model_planner.py`). The default model is presumed
sufficient; upgrades are scored and justified per task, mechanical roles drop
below the default, and escalation happens only between attempts after a failure.
Decisions persist, so a resumed run does not re-decide and thereby invalidate
its own ledger.

### What the upgrade's tests found

Writing the tests found five real defects, all fixed:

1. **The coherence rule overruled an explicit negation.** A brief saying "no
   API, no interface" still got a Backend Engineer, because the rule
   "a model with no surface cannot be demonstrated" fired unconditionally.
   Coherence repair now yields to a capability the brief ruled out.
2. **The `data` capability was undetectable.** `\bchart\b` and `\bcsv\b` never
   match "charts" and "CSVs", which is how briefs actually write them. Every
   data-analysis brief was being read as having no data in it.
3. **Endpoints did not survive compression.** `POST /triage` matched no
   keep-pattern, so an interface definition could be compressed away — the exact
   class of loss the compressor exists to prevent.
4. **The secret scan inspected only what it had already decided was safe.** A
   `.env` was excluded from the package by pattern and therefore never scanned,
   so packaging succeeded while the file sat in the directory `git add -A` would
   run over. The scan now covers the whole working tree.
5. **`github prepare` reported a built package as unbuilt**, because
   `write_status` defaulted `built=False` for callers that had no opinion about
   it.

A sixth issue was found and fixed by the resume test: context was built from the
run's last five handoffs, which change on every wave. That made every context
volatile, which changed the ledger fingerprint, which made a resumed run redo
work it had already paid for. Upstream context now comes from the task's own
dependencies — more relevant *and* stable.

---

## The honest limitation, first

**No *full* run has been executed against a live model.** The system now runs
on the operator's Claude subscription through the Claude Agent SDK, and single
specialists have been verified end to end on it -- tools called through the real
registry, write scoping enforced, artifact contract satisfied, handoff recorded,
rate-limit telemetry observed live. Every *whole-hackathon* end-to-end run so
far used `SimulatedBackend`.

What that **does** prove, because the simulated backend drives the real tool
layer:

- ✅ Tool allowlisting and write scoping are enforced
- ✅ Artifact contracts and postconditions catch missing and stub work
- ✅ The task graph orders, parallelises and blocks correctly
- ✅ Self-correction re-queues, then abandons, and records reasoning
- ✅ Real `.pptx`, `.zip` and file artifacts are produced
- ✅ Secret exclusion works on the packaging path
- ✅ State persists and reloads

What it **does not** prove:

- ❌ That Claude, given these missions and tools, produces *good* research,
  architecture, code or pitches. Content quality is entirely untested.
- ❌ That the prompts elicit correct tool sequencing from a real model.
- ❌ Real token costs and latency.

Every simulated artifact says this in its own first paragraph. The deck's
results slide contains `TODO: real measured figure — none available in a
simulated run` rather than an invented number.

**To run for real:** log in once with `claude`, confirm with
`python hackathon.py auth`, then `python hackathon.py --project <dir> run`.
No API key, no per-token billing; usage counts against the plan's windows. A
full run will exhaust a five-hour window on a Pro plan, and stops cleanly when
it does -- completed tasks replay from `AGENT/cache/ledger.json` on resume.

---

## The synthetic hackathon

A fictional brief — *MediRoute*, a clinic triage assistant — with judging
criteria, submission requirements and constraints, at `synthetic_test/`.

```
Orchestrator selected 27 of 28 specialists  (database_engineer skipped)
27 tasks · 7 waves · 100% complete · 104 tool calls · 0 failures
```

| Wave | Ran concurrently |
|---|---|
| 1 | requirements, user_research, tech_research, competition, market |
| 2 | product_plan, strategy |
| 3 | architecture, pitch, ux, brand |
| 4 | test, ai, backend, ml, docs, security |
| 5 | demo, req_audit, slides, integrate, frontend, code_review |
| 6 | final_audit, devops, ui |
| 7 | submission |

Produced 37 files, including a real 7-slide `presentation.pptx` (44 KB) and a
`submission.zip` (35 files, 63 KB) verified to contain `README.md` and the deck
and to exclude `.env`.

Against the brief's checklist: understand ✅ research ✅ ideate ✅ choose ✅
architect ✅ design ✅ implement ✅ test ✅ debug ✅ document ✅ demo ✅ pitch ✅
audit ✅ package ✅ — with the caveat above that "implement" means *contract-
valid placeholder code*, not a working triage system.

### Re-run after the 2026-09-05 upgrade

The same shape of brief, re-run through the full new pipeline
(`init → plan → run → package → github init → prepare → status`):

```
27 of 28 specialists selected   (ml_engineer skipped: no ML or vision signal)
27 tasks · 7 waves · 100% complete · 102 tool calls · 0 failures · 5.6s
models          sonnet 19 · opus 5 · haiku 3
context         ~40,640 tok sent · ~4,436 removed · compression ratio 0.90
                120 duplicate blocks removed across the run
package         36 files, 105 KB · secret scan PASS
github          repo initialised, validation OK, nothing pushed
```

Two things worth noting from that run. The model split is the point of the
model planner: 19 tasks on the default, 5 upgraded with a stated reason, 3
dropped below it — where previously all 27 would have followed a fixed
per-role table with 7 roles permanently on Opus.

And re-planning the finished project replayed **22 of 27 tasks from the ledger
with no model call**. The 5 that re-ran are the ones whose available context
genuinely grew — the Pitch Strategist, for instance, first ran before the test
report existed, so on replay it sees evidence it did not have. That is the
ledger being correct rather than the ledger failing.

---

## Bugs the synthetic run found and fixed

This is what the end-to-end test was for. Eight real defects, none visible from
reading the code.

**1. Parallel results filed under the wrong task** *(most serious)*

Wave 1 reported `user_research → RESEARCH/technical_research.md`. `run_wave`
regroups tasks by write-scope overlap, then `step()` zipped results against the
*original* order. Every result landed on a neighbour's task. Invisible while
everything passed; on a failure it would have marked the wrong agent failed and
the wrong subtree unreachable.
→ Results are now paired with their task at dispatch.
→ Regression: `test_parallel_results_are_paired_with_their_own_task`

**2. Specialist selection was negation-blind**

"No interface required" selected the frontend team. "No backend, no database"
selected both. The selector matched keywords with no regard for the clause
around them, staffing teams for work the brief explicitly ruled out.
→ `_negated()` looks back to the clause boundary for a negator.
→ Regression: `test_negation_is_respected`, `test_ml_brief_does_not_staff_a_frontend`

**3. Selection read only the problem statement**

Constraints ("Python-only backend") and judging criteria ("can a judge run it
from the repo") carry real staffing signal and were being ignored.
→ `ProjectState.full_brief` concatenates all four brief files.

**4. UX/UI designers selected with no engineer to build their spec**

A coherence gap: the design signal fired on "usable" while the frontend signal
did not fire at all, commissioning specs nobody would implement.
→ Three coherence rules now repair unshippable combinations.
→ Regression: `test_design_work_always_gets_someone_to_build_it`

**5. `UnicodeEncodeError` killed the CLI on Windows**

`✓ ○ ━ █` cannot be encoded in cp1252; the dashboard crashed on the platform
this was built on.
→ `glyphs.py` probes stdout, upgrades to UTF-8 where possible, falls back to
ASCII where not, and `say()` never dies on an un-encodable character.

Three further fixes, found while testing rather than by the run:

**6. `.env.example` was stripped from submissions.** The `.env.*` secret glob
matched the template file that *documents* required configuration — exactly
what a judge needs. Added an explicit exception.
→ `test_zip_keeps_env_example_but_drops_env`

**7. Concurrent ledger writes could lose sources.** Four research agents append
to `RESEARCH/sources.json` in the same wave. Added a lock, and grouped
overlapping write scopes so they serialise regardless.

**8. A relative project root rejected every path in its own project.**
`ExecutionContext` compared resolved absolute paths against an unresolved root,
so `root=Path(".")` made `is_relative_to` false for everything. Invisible via the
CLI, which always resolves; fatal for any library caller.
→ Root is normalised in `__post_init__`.
→ Regression: `test_relative_root_is_normalised`

---

## Verifying the claim "these are real agents"

```bash
python -m pytest tests/test_boundaries.py -v
```

- `test_researcher_cannot_call_shell` — the tool is never sent, and a direct
  call raises `ToolDenied`
- `test_researcher_cannot_write_source` — returns an error string; file is not created
- `test_tester_cannot_patch_the_code_it_tests` — the boundary that stops a
  tester making reports green instead of products work
- `test_path_escape_is_rejected` — `../` cannot leave the project
- `test_specialists_are_actually_distinct` — no two of the 28 share a
  `(tools, write_paths, produces, requires)` signature
- `test_completed_without_artifacts_is_downgraded` — a `completed` claim with
  no files becomes `FAILED`
- `test_postconditions_catch_a_document_missing_its_topics` — 500 words of
  Lorem ipsum fails the market-report contract

---

## Known limitations

1. **No live-model validation.** Stated above. This is the gap that matters, and
   it now extends to the capability planner: `Backend.ask_json` is exercised
   only through `SimulatedBackend`, whose planner re-derives its answer from the
   deterministic rules. The merge logic, the guardrails and the JSON parsing are
   genuinely tested; the *judgement* the real planner would add is not.
2. **Stage-1 selection is still keyword-based.** Negation-aware, and now backed
   by a Claude planner, but the deterministic pass remains shallow and is what
   runs when the planner is unavailable or `--no-planner` is set. The plan is
   written to `AGENT/plan.md` with reasons precisely so a human can correct it.
3. **`Custom` postconditions are invisible to the simulated backend.**
   `simulation.py` introspects `FileContains`/`HasHeadings`/`MinWords`/
   `ValidJson` but cannot read an arbitrary callable — this is what caused the
   slide-count failure mid-build. Formats with their own grammar need their own
   generator (as `slides.md` now has). Adding a `Custom` check to a spec may
   require a matching synthesizer branch.
4. **Retry is bounded at 2 attempts** and re-runs the same specialist with the
   failure appended. It does not try a different agent or a different approach.
5. **No live deployment.** The DevOps Engineer writes config and a setup guide;
   nothing is deployed anywhere.
6. **`web_search` is untested** — it is server-side and needs a live API call.
7. **Two roster gaps are real and declared, not staffed.** There is no hardware
   or data-analysis specialist. An IoT brief and a data-analysis brief therefore
   staff the same generalist team, and `plan` reports the gap rather than
   pretending a specialist covers it.
8. **Token counts are estimates.** A local heuristic, not a tokenizer, deliberately
   — the alternative was a paid counting API to save subscription usage. Expect a
   few per cent of error in `status`; the measured counts from `state.cost()` are
   reported alongside.
9. **`context_tokens_removed` is measured against the candidate set**, i.e. what
   an unoptimised system would have sent. On the synthetic run that is ~10%,
   almost all from deduplication, because the simulated artifacts are small
   enough that compression rarely triggers. Real artifacts should shift the
   balance towards compression; that is unmeasured.
10. **`github push` cannot create a remote without `gh`.** Without the GitHub
    CLI it commits locally and stops with instructions, which is the correct
    failure but is not a complete publish path.

---

## Reuse from prior hackathons

Logged in `AGENT/reference_decisions.md`, and applied throughout:

| From | Used for |
|---|---|
| `claude-agent-scaffold` (`.knowledge/`) | The `tool_runner` loop, `@beta_tool` contract, workspace sandboxing, 30k truncation, env-var secret substitution — the whole `AnthropicBackend` and the shape of `tools/base.py` |
| `patterns.md` — *errors as strings, never raise* | Every tool returns `fail(e)`; `test_tools_return_errors_rather_than_raising` |
| `patterns.md` — *truncate tool results* | `truncate()` at 30k; `test_result_truncation_protects_the_context_window` |
| `patterns.md` — *optimise the metric you are scored on* | The ML Engineer's mission, verbatim as its first rule |
| `patterns.md` — *publish trivial baselines* | The Presentation Builder's results-slide instruction |
| `patterns.md` — *name your assumptions* | `assumptions` is a first-class handoff field the Final Auditor reads |

---

## Next steps

1. **Run it against a live model** on a real brief. Everything above is
   machinery; the content is unproven.
2. Compare simulated and live artifacts for the same brief to see which mission
   prompts actually elicit good work.
3. Write `AGENT/postmortem.md` after the first real hackathon and promote what
   holds into `.knowledge/patterns.md` with a confidence level.
4. ~~Consider LLM-assisted specialist selection as a second opinion on the
   keyword pass.~~ Done — `planner.py`, 2026-09-05. Still needs a live run to
   show whether its judgement beats the regexes on a brief they disagree about.
5. Measure the optimiser against real artifacts. The synthetic run's documents
   are small, so compression barely fires and the reported saving is almost all
   deduplication.
6. Consider a data-analysis specialist and a hardware/firmware specialist —
   currently the two declared roster gaps.
