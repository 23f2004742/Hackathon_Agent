# AGENTS.md — The Specialist Roster

28 specialists across 7 teams, coordinated by an Orchestrator.

A specialist here is **not a system prompt**. It is a bundle of five things the
runtime enforces in code:

| Dimension | Enforced by | What happens when violated |
|---|---|---|
| **Tools** | `tools/base.py::guard` + per-agent allowlist | The tool is never sent to the model; a direct call raises `ToolDenied` |
| **Write scope** | `ExecutionContext.resolve_for_write` | Write returns an error string naming the agent's real scope |
| **Required inputs** | `AgentSpec.missing_inputs` | Task returns `BLOCKED` before a single token is spent |
| **Produced artifacts** | `AgentResult.validate_against` | A `completed` claim is downgraded to `FAILED` if files are absent or stubs |
| **Postconditions** | `AgentSpec.check_postconditions` | Task fails with the specific reason (missing topic, bad JSON, unparseable code) |

Verify it yourself:

```bash
python -m pytest tests/test_boundaries.py -v
```

`test_specialists_are_actually_distinct` asserts no two agents share a
`(tools, write_paths, produces, requires)` signature. It currently passes for
all 28.

---

## The handoff protocol

Every specialist reports through one tool call — `submit_handoff` — not prose.
The Orchestrator reads the structure, never the narrative.

```json
{
  "status": "completed | failed | blocked | needs_human | skipped",
  "summary": "what you did and what the next specialist needs to know",
  "artifacts": [{"path": "...", "kind": "markdown", "bytes": 0}],
  "findings":  [{"summary": "...", "severity": "CRITICAL|HIGH|MEDIUM|LOW|INFO",
                 "evidence": "...", "source": "..."}],
  "decisions": [{"what": "...", "why": "...", "alternatives": [], "reversible": true}],
  "assumptions": ["anything assumed rather than verified"],
  "risks": ["what could still go wrong"],
  "next_tasks": [{"agent": "...", "objective": "...", "priority": "...", "reason": "..."}],
  "blocked_by": ["what you needed and lacked"]
}
```

Two properties matter:

- **`artifacts` is rewritten from disk.** Whatever an agent claims, the runner
  replaces the list with what it actually finds. An agent cannot report a file
  it did not write.
- **`next_tasks` are proposals, not commands.** Only the Orchestrator schedules
  work, and it accepts only CRITICAL/HIGH proposals naming a selected agent.
  This is how a Tester's FAIL becomes a Developer fix task without letting any
  agent inject work into the graph directly.

---

## RESEARCH

Shared shape: web access, a provenance ledger, and **no shell, no code write
access**. A researcher who wants to change an implementation must hand off.

Tools: `web_search`, `fetch_url`, `record_source`, `list_sources`, `read_file`,
`write_file`, `knowledge_search` (+ `search_code` for the technical researcher).

| Agent | Produces | Must cover | Requires |
|---|---|---|---|
| `market_researcher` | `RESEARCH/market_report.md` | market size, customer, business model, adoption | problem statement |
| `competitor_researcher` | `RESEARCH/competitive_analysis.md` | competitor, pricing, weakness, differentiation | problem statement |
| `technical_researcher` | `RESEARCH/technical_research.md` | recommended, rejected, risk, dataset | problem statement |
| `user_researcher` | `RESEARCH/user_research.md` | target user, workflow, pain point, journey, adoption | problem statement |

**The provenance ledger** (`RESEARCH/sources.json`) is the mechanism behind
"the pitch invents nothing". `record_source` distinguishes a *sourced* claim
from an *estimate*; `verify_claims` later lints any document for numeric or
superlative claims with no matching ledger entry. The Final Auditor runs it.

---

## PRODUCT

No shell, no code write access — deliberately. An agent that can quietly start
building stops making scope decisions honestly.

| Agent | Produces | Must cover | Requires |
|---|---|---|---|
| `requirements_analyst` | `PRODUCT/requirements.md` | functional, non-functional, constraints, acceptance criteria, judging criteria, submission | problem statement |
| `product_manager` | `PRODUCT/product_plan.md` | MUST/SHOULD/NICE/**DO NOT BUILD**, MVP, user stories, success metrics | requirements |
| `strategist` | `PRODUCT/strategy.md` | positioning, differentiation, business model, impact, judging criteria, **weakest** | requirements |

The `DO NOT BUILD` list and the strategist's `weakest` section are enforced by
postcondition — the documents fail validation without them. They are the two
sections teams skip and most need.

---

## ENGINEERING

The only agents with `run_shell`, `run_tests` and source write access. Scopes
are **disjoint by layer** so two agents cannot silently overwrite each other.

| Agent | Write scope | Produces | Postcondition |
|---|---|---|---|
| `architect` | `PRODUCT/architecture.md` | architecture doc | covers component, data flow, deployment, trade-off, **deliberately left out** |
| `backend_engineer` | `src/backend/`, `tests/backend/` | `src/backend/api.py` | parses as Python |
| `frontend_engineer` | `src/frontend/`, `tests/frontend/` | `src/frontend/index.html` | contains `<html>` |
| `ml_engineer` | `src/ml/`, `data/`, `VALIDATION/ml_eval.md` | pipeline + evaluation | parses; eval covers metric, baseline |
| `ai_engineer` | `src/ai/`, `tests/ai/` | `src/ai/agent.py` | parses as Python |
| `database_engineer` | `src/db/`, `data/` | `src/db/schema.sql` | contains `create table` |
| `developer` | `src/`, `tests/`, `run.py` | — (generalist / fix tasks) | — |
| `devops_engineer` | `Dockerfile`, `requirements.txt`, `.env.example`, `scripts/` | setup guide + env template | setup covers install, run |

The ML Engineer's mission carries the highest-confidence lesson in the shared
knowledge base: **optimise the metric you are scored on, not the conventional
one for the task.** It runs at `effort=xhigh`.

---

## DESIGN

Specification writers. They do not write application code — the Frontend
Engineer builds to their spec.

| Agent | Produces | Must cover |
|---|---|---|
| `ux_designer` | `DESIGN/ux.md` | user journey, information architecture, onboarding, empty state, error state |
| `ui_designer` | `DESIGN/ui.md` | typography, spacing, color, component, responsive, **contrast** |
| `brand_designer` | `DESIGN/brand.md` | product name, tagline, positioning |

`brand_designer` runs on `claude-sonnet-5` rather than Opus, and its mission
says "timebox this hard" — branding is the cheapest place in a hackathon to
lose three hours.

---

## VALIDATION

| Agent | Write scope | Produces | Notably cannot |
|---|---|---|---|
| `tester` | `tests/`, `VALIDATION/test_report.md` | test report (PASS/FAIL/BLOCKED/N-A) | **write `src/`** |
| `code_reviewer` | `VALIDATION/code_review.md` | severity-ranked findings | write any code |
| `security_reviewer` | `VALIDATION/security_review.md` | triaged scan results | write any code |
| `requirements_auditor` | `VALIDATION/requirements_audit.md` | requirement → evidence map | write any code |

**The Tester cannot edit the code it tests.** This is the single most important
boundary in the system: an agent that can patch what it is testing will make
the report green rather than make the product work. Failures leave as
`next_tasks` for the Developer.

The Security Reviewer gets real scanners (`scan_secrets`, `scan_code_security`,
`check_dependencies`) that return `file:line` hits. The model's job is triage —
telling a placeholder in `.env.example` from a live key in a committed file.

---

## COMMUNICATION

| Agent | Produces | Must cover |
|---|---|---|
| `technical_writer` | `README.md`, `DOCUMENTATION/technical.md` | install, run, what it does |
| `pitch_strategist` | `PRESENTATION/pitch_strategy.md` | judging criteria, evidence, differentiation, narrative, impact |
| `presentation_builder` | `PRESENTATION/slides.md` + `presentation.pptx` | ≥ 6 slides, rendered via `build_pptx` |

Pitch roles hold `verify_claims` and the ledger, and **nothing that can change
the product**. If the pitch needs a stronger fact, the answer is more evidence,
not better adjectives.

---

## DELIVERY

| Agent | Produces | Must cover |
|---|---|---|
| `demo_engineer` | `DEMO/demo_script.md` | setup, steps, expected output, **fallback**, narration |
| `final_auditor` | `FINAL/final_audit.md` | problem fit, innovation, technical depth, demo, impact, judging criteria, submission, CRITICAL |
| `submission_manager` | `SUBMISSION/submission_manifest.json` + `checklist.md` | valid JSON with `deliverables`, `verified` |

The Final Auditor is briefed as a hostile judge and runs at `effort=xhigh`. Its
CRITICAL findings surface as human-in-the-loop gates and block submission.

`build_zip` excludes secret-shaped files unconditionally — `.env`, `*.pem`,
`*.key`, `*credentials*` — and reports what it dropped. `.env.example` is an
explicit exception, because it documents required config and belongs in the
submission.

---

## Which agents actually run

Never all 28. Selection is two stages, in `planner.py`.

**Stage 1 — deterministic.** `select_by_rules` reads the **full brief** —
problem statement, constraints, judging criteria and submission requirements —
and detects capability signals, respecting negation:

```bash
python hackathon.py select "Static site for a food bank. No backend, no database."
# frontend + ux + ui, no backend, no database, no ml

python hackathon.py select "Tabular leaderboard task. No interface required."
# ml_engineer, no frontend, no designers, no backend
```

**Stage 2 — the Claude capability planner.** `python hackathon.py plan` (or
`select --planner`) makes one toolless call carrying the brief and the roster
and nothing else, and gets back a structured staffing plan with a reason for
every inclusion and every exclusion. It may add specialists the regexes missed
and remove ones they guessed at, but not:

- remove the mandatory delivery spine for the project type;
- overrule a capability the brief states outright;
- name a specialist that is not in the roster.

Coherence rules then repair combinations that cannot ship, whoever proposed
them:

1. UX/UI spec commissioned → a Frontend Engineer must exist to build it.
2. A model with no backend or frontend → add a surface, or it cannot be demoed
   (unless the brief explicitly ruled one out).
3. Brief judged on reproducibility → add the DevOps Engineer.
4. A deck with no pitch strategy behind it → add the Pitch Strategist.

Full reasoning goes to `AGENT/plan.md` and to `ProjectState.selection`,
including a *Deliberately not activated* section naming, for each skip, the
capability that would have selected that specialist.

**Replanning.** If a specialist discovers mid-run that the project needs a
capability nobody staffed, the Orchestrator adds exactly the missing
specialists and their tasks. Completed work is untouched; only what depends on
the new tasks waits. A capability the brief ruled out is not replannable.

### Two declared gaps

The roster has no hardware/firmware specialist and no data-analysis specialist.
An IoT brief and a data-analysis brief therefore staff the same generalist
team, and `plan` says so under *Gap* rather than quietly pretending the project
has no hardware or no data in it.

### Which model each specialist runs on

`routing.py` sets effort and turn limits per role. `model_planner.py` chooses
the model per *task*, starting from the configured default and upgrading only
where it can justify it:

```bash
python hackathon.py models          # the catalogue and the policy
python hackathon.py plan            # the per-task decision, with reasons
python hackathon.py run --model opus   # override everything, deliberately
```

---

## Adding a specialist

Add an `AgentSpec` to the relevant `agents/<team>/specs.py`:

```python
DATA_ENGINEER = AgentSpec(
    name="data_engineer",
    title="Data Engineer",
    team="engineering",
    mission="""What this role owns, written as instructions to the model...""",
    tools=("read_file", "write_file", "run_shell", "read_data", "write_csv"),
    write_paths=("src/pipeline/", "data/"),
    requires=("PRODUCT/architecture.md",),
    produces=("src/pipeline/ingest.py",),
    postconditions=(_python_parses("src/pipeline/ingest.py"),),
    context_keys=("architecture", "requirements"),
)
```

Then append it to that module's `SPECS`. Import-time validation rejects unknown
tools, artifacts declared outside the write scope, and `produces` with no
`write_paths` — so a malformed specialist fails at startup, not at hour 18.

To schedule it, add a row to `BLUEPRINT` in `orchestrator.py` and a line to
`OBJECTIVES`.
