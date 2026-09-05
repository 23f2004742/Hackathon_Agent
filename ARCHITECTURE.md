# ARCHITECTURE.md

The Hackathon Agent OS: how a problem statement becomes a validated submission.

## The planning pipeline

Everything between a problem statement and a GitHub-ready output, in the order
it happens. Each stage is inspectable (`hackathon plan`) and persisted, so a run
that stops halfway resumes rather than restarts.

```
  PROBLEM STATEMENT
        │
        ▼
  PROJECT ANALYSIS          planner.analyse   — type, complexity, constraints
        │
        ▼
  CAPABILITY DETECTION      planner.detect    — negation-aware, deterministic
        │
        ▼
  CLAUDE CAPABILITY         planner.plan_with_claude — one toolless call,
  PLANNER                                       brief + roster only
        │
        ▼
  SPECIALIST SELECTION      planner.apply_plan — merged under code guardrails
        │
        ▼
  TASK GRAPH                orchestrator.build_plan
        │
        ▼
  MODEL PLANNER             model_planner      — default unless justified
        │
        ▼
  TOKEN / CONTEXT           token_optimizer    — prioritise, dedupe, compress
  OPTIMIZER
        │
        ▼
  SPECIALIST EXECUTION      waves, parallel where write scopes are disjoint
        │
        ▼
  CHECKPOINT                state.save() after every wave
        │
        ▼
  VALIDATION                artifact contracts + postconditions
        │
        ▼
  REPLAN IF REQUIRED        consider_replan — adds tasks, never restarts
        │
        ▼
  PACKAGE                   packaging.build_package + secret scan
        │
        ▼
  FINAL AUDIT               final_auditor, as a hostile judge
        │
        ▼
  GITHUB-READY OUTPUT       github init / prepare / push
```

## The shape of it

```
                              USER
                                │
                                ▼
                    ┌───────────────────────┐
                    │     ORCHESTRATOR      │   selection · scheduling ·
                    │   (no tools of its    │   failure judgement ·
                    │        own)           │   replanning
                    └───────────┬───────────┘
                                │ builds
                                ▼
                    ┌───────────────────────┐
                    │      TASK GRAPH       │   dependency-aware, priority-
                    │  ready() → waves      │   ordered, parallel-capable
                    └───────────┬───────────┘
                                │ dispatches
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
   ┌─────────┐            ┌──────────┐           ┌────────────┐
   │RESEARCH │            │ PRODUCT  │           │ENGINEERING │
   │ 4 specs │            │ 3 specs  │           │  8 specs   │
   └────┬────┘            └────┬─────┘           └─────┬──────┘
        │                      │                       │
        │                 ┌────▼─────┐           ┌─────▼──────┐
        │                 │  DESIGN  │           │ VALIDATION │
        │                 │ 3 specs  │           │  4 specs   │
        │                 └────┬─────┘           └─────┬──────┘
        │                      │                       │
        └──────────────────────┼───────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │ COMMUNICATION (3)    │
                    │ DELIVERY (3)         │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │   SPECIALIST RUNTIME │
                    └──────────┬───────────┘
                               │ every call passes through
                               ▼
                    ┌──────────────────────┐
                    │  ExecutionContext    │  ◄── the access boundary
                    │  · tool allowlist    │
                    │  · write scope       │
                    │  · approval mode     │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │   TOOL REGISTRY (27) │
                    └──────────────────────┘
```

## Modules

| Module | Responsibility |
|---|---|
| `orchestrator.py` | The unified planner: analysis → selection → graph → models → budgets; wave execution, self-correction, replanning, human gates |
| `planner.py` | Two-stage specialist selection — deterministic capability detection, then the Claude capability planner, merged under guardrails |
| `model_planner.py` | Which model runs each task, defaulting down and upgrading only on stated evidence |
| `token_optimizer.py` | Context prioritisation, deduplication, structure-preserving compression, budgets and metrics |
| `packaging.py` | Submission package construction, secret scanning, inclusion/exclusion rules |
| `github.py` | `init` / `prepare` / `push`, with confirmation gating on the only outward-facing step |
| `taskgraph.py` | `Task`, `TaskGraph`; cycle detection, `ready()`, blocked propagation, priority ordering, persistence |
| `agents/base.py` | `AgentSpec`, `Specialist` runner, declarative postcondition checks |
| `agents/<team>/specs.py` | The 28 specialist definitions |
| `handoff.py` | `AgentResult` and the contract validator that downgrades unearned success |
| `tools/base.py` | `ExecutionContext`, the registry, `guard`, write scoping |
| `tools/*.py` | 27 tools across 13 categories |
| `context.py` | Targeted three-layer context assembly |
| `state.py` | `ProjectState`, persisted to `AGENT/state.json` |
| `llm.py` | `AnthropicBackend` (real) and `SimulatedBackend` (testing) |
| `simulation.py` | Contract-satisfying artifact synthesis for the simulated backend |
| `dashboard.py` / `glyphs.py` | Status rendering, with ASCII fallback for legacy consoles |
| `cli.py` | The `hackathon` command surface |

### A deviation from the brief, stated plainly

The brief sketched one file per agent (`research/market_researcher.py`, …).
This implementation groups each team's specs into one `specs.py` — 7 files
rather than 28. Each spec is a ~30-line declaration; one-per-file would have
added 21 files of import boilerplate with no navigational gain, and the brief
itself says *"adapt the exact structure to the existing repository, avoid
unnecessary duplication."* `python hackathon.py agents <name>` is the lookup
path, not the filesystem.

## The three enforcement points

Everything that makes this a multi-agent system rather than a prompt collection
happens in three places.

**1. Tool allowlisting** — `tools/base.py::guard`

A specialist is handed only the tools its spec names; the model cannot call what
it was never sent. `guard()` re-checks at call time as defence in depth.

**2. Write scoping** — `ExecutionContext.resolve_for_write`

Every write resolves against the agent's `write_paths`. Out-of-scope writes
return an error string naming the agent's real scope and suggesting a handoff —
readable by the model, so it adapts rather than crashing.

**3. Artifact contracts** — `AgentResult.validate_against`

After an agent stops, the runner checks the filesystem. A `completed` status
with missing or stub-sized artifacts becomes `FAILED`, and the reported artifact
list is rebuilt from disk. Then declarative postconditions run: does the market
report mention adoption, does the manifest parse as JSON, does the generated
Python compile.

An agent cannot talk its way past any of these.

## Execution model

`ready()` returns tasks whose dependencies **completed** *and* whose required
input files **exist on disk**. The double check matters: an agent that reported
success without writing anything does not unblock its dependants.

Tasks in a wave are grouped by write-scope overlap. Disjoint scopes run
concurrently on a `ThreadPoolExecutor`; overlapping ones serialise, because two
agents appending to the same provenance ledger is a lost-update bug.

Results are paired with their task **at dispatch**, not zipped by position
afterwards — grouping reorders the wave, and positional pairing files each
agent's result under a neighbour's task. That was a real bug the synthetic
hackathon caught; `test_parallel_results_are_paired_with_their_own_task` is the
regression.

The tool `ExecutionContext` is a `threading.local`, so parallel specialists
carry their own boundaries.

## Self-correction

A failed task does not stop the run and does not silently pass. The Orchestrator:

1. reads the concrete reason from the validator (`slides.md has 4 slides, expected 6`);
2. computes what it blocks downstream;
3. writes both to `AGENT/decision_log.md`;
4. re-queues the task once, with the failure appended to its objective so the
   retry is not a blind repeat;
5. abandons it after `MAX_ATTEMPTS`, letting `blocked()` mark the subtree
   unreachable rather than pretending the work happened.

## Memory layers

| Layer | Location | Scope |
|---|---|---|
| Global | `hackathon/.knowledge/` | Lessons and reusable components across all hackathons. Read-only to specialists via `knowledge_search`. |
| Project | `<project>/AGENT/` | `state.json`, `plan.md`, `decision_log.md`, `reference_decisions.md` |
| Task | assembled per run | Only the `context_keys` a spec declares |

Context slices are **digests** — headings plus a bounded excerpt. A specialist
that needs the full document calls `read_file`. This is why a Brand Designer
never receives the test report.

Those digests are the *candidate* set. `token_optimizer.py` then prioritises,
deduplicates, compresses and trims them to a per-task budget before anything is
sent — see *Cost control*. A task's upstream handoffs come from its own
dependencies rather than the run's last five results: partly relevance, and
partly because a context built from the run's tail changes on every wave, which
changes the ledger fingerprint and makes a resumed run redo work it had already
paid for.

## Cost control

Four layers, in the order they save the most.

**1. Do not staff the specialist.** Selection activates a subset of the 28; the
rest never run. See *Specialist selection*.

**2. Do not send the context.** `token_optimizer.py` sits between
`ContextBuilder` and the specialist. Every candidate slice carries a priority
band — task requirements, direct dependencies, relevant artifacts, project
state, knowledge, prior hackathons, background. The optimiser deduplicates
paragraphs across slices (the same constraint appears in the state, a handoff
and an artifact digest, and is sent once at the highest band that carries it),
compresses anything oversized, and drops the lowest bands when the budget bites.
Band 1 is never dropped: a specialist that does not understand its own task
wastes a whole run, which costs more than any saving.

Compression is subtractive, not positional. It *keeps* the lines carrying
decisions, interfaces, endpoints, constraints, file paths, errors, unresolved
issues and acceptance criteria, and drops the connective prose between them.
Truncation would have thrown away the acceptance criteria at the foot of the
document — exactly what the next specialist needed. The result is labelled
`[compressed ...]` so the agent knows to call `read_file` rather than assuming
it received the whole document.

Budgets are per task, not global: `budget_for` sizes `context_budget`,
`output_budget` and `research_budget` from the task's own effort, impact and
priority plus the project's complexity. A specialist with no research tools gets
a research budget of zero.

**3. Do not pay twice.** The task ledger replays completed work with no model
call (see *Memory layers*). A second content-addressed cache at
`AGENT/cache/context_cache.json` stores compressions and reusable research, so
the same 40KB architecture document is summarised once rather than for each of
the eight specialists that depend on it.

**4. Do not over-buy the model.** See *Model selection*.

Metrics — `estimated_input_tokens`, `estimated_output_tokens`,
`context_tokens_removed`, `cache_hits`, `cache_misses`,
`context_compression_ratio` — accumulate in `ProjectState.token_metrics` and are
reported by `hackathon status`. Token counting is a local estimator, not a
tokenizer: requiring a paid counting API in order to save subscription usage
would be a strange trade, and a few per cent of error changes no decision here.

## Specialist selection

Two stages, merged under guardrails held in code.

**Stage 1, deterministic.** Negation-aware regexes over the whole brief produce
a capability map, a project type, a complexity score, and an explicit list of
capabilities the brief *rules out*. "No backend, no database" is information,
and it is kept distinct from "backend was never mentioned".

**Stage 2, the Claude capability planner.** One toolless call
(`Backend.ask_json`) receives the brief and the roster — not the project, not
the repository, not previous agent output — and returns a structured plan:
required specialists with reasons and priorities, exclusions with reasons, and a
project type. One planning call that removes eight specialists pays for itself
many times over.

The merge rules are the interesting part, because a planner allowed to do
anything is a planner that can staff a team which cannot ship:

- it may **add** specialists the regexes missed;
- it may **remove** specialists the regexes guessed at;
- it may **not** remove the mandatory delivery spine for the project type
  (`MANDATORY_BY_TYPE`);
- it may **not** overrule a capability the brief states outright;
- anything it names that is not in the roster is dropped, never invented.

A final coherence pass repairs teams that cannot physically deliver — a UX spec
commissioned with nobody to build it, a deck with no pitch behind it.

Every inclusion and exclusion carries a specific reason, and both persist to
`ProjectState.selection`. "Not required" is not a reason; "no database signal in
the brief" is one a human can disagree with.

**Replanning.** When a handoff reveals a capability nobody staffed,
`consider_replan` adds exactly the missing specialists and exactly the blueprint
tasks that need them. Completed work is never touched, the graph is
re-validated, and the new tasks get their own model decision and budget. A
capability the brief explicitly ruled out is not replannable: an agent's opinion
does not outrank the organisers.

## Model selection

`routing.py` still decides what *kind* of work a role does, and therefore its
effort and turn limit. `model_planner.py` decides which model runs it, and
starts from the opposite premise: the default model is presumed sufficient, and
anything stronger has to be argued for on this specific task.

That is a change. Previously seven specialists were pinned to Opus for the life
of the system, so a routine architecture decision in a three-file CLI burned the
plan's separate weekly Opus window exactly as hard as a genuinely hard one.

A task scores 0–10 on reasoning weight, priority, effort, project complexity,
previous attempts and domain risk. At or above `UPGRADE_AT` it moves one step up
the ladder; mechanical roles with no judgement to buy move one step down;
everything else stays on the default. Each decision carries a reason and a
confidence and is written to `ProjectState.model_decisions`.

Once a task starts on a model it stays there. Escalation happens only between
attempts, after a failure — switching models partway through a task throws away
everything the first one had worked out — and is recorded as `from`, `to` and
`reason`.

Model identifiers are configurable (`HACKATHON_DEFAULT_MODEL`,
`HACKATHON_MODELS`) because they go stale, and an unknown name is rejected
rather than silently falling back to the default: a typo in `--model` that fell
through would spend a whole run on the wrong model and look like it worked.

## Packaging and GitHub

`packaging.py` builds `dist/submission/` from the project tree — source, tests,
docs, demo, presentation and the submission artifacts, laid out for a reader
rather than for the agents. Exclusion is the default and inclusion is a
decision, so a new kind of cache directory does not quietly end up in the
archive. Project-specific overrides live in `AGENT/package_rules.json`.

The secret scan runs over the **whole working tree**, not only the files that
would be copied. A `.env` is excluded from the package by pattern, but it is
still sitting in the directory `git add -A` runs over, and a scan that inspects
only the files it already decided were safe proves nothing. A live-looking hit
blocks the package; `--force` overrides it and is recorded in the manifest.

`github.py` is three verbs that each stop rather than assume. `init` creates the
repository, a secrets-aware `.gitignore` and a README if there is none, then
validates the result. `prepare` shows exactly what would be committed — asked of
git itself rather than reconstructed, because git is what will do the commit.
`push` is the only outward-facing action in the system: it refuses without
explicit confirmation, refuses on a failed scan, and stores no credential of its
own. It shells out to `git` and, when present, `gh`; both already hold the
operator's credentials, and neither hands them to us.

## Backends

`AnthropicBackend` uses `client.beta.messages.tool_runner` with history
mirroring, `pause_turn` restarts, refusal fallback and typed error handling —
carried over from the scaffold indexed in `.knowledge/`, and logged in
`AGENT/reference_decisions.md`.

`SimulatedBackend` drives the same specialists through the same tool layer with
no API: it reads required inputs, consults prior art, writes each declared
artifact and submits a handoff. Boundaries, contracts and packaging are
genuinely exercised; only judgement is absent. Every document it writes says so
in its first paragraph.

`SubscriptionBackend` is the real one, and the default. It drives the same
specialists through the Claude Agent SDK, which spawns the Claude Code CLI and
authenticates with the operator's own Claude subscription -- no API key is read
anywhere in the system.

The SDK arrives with its own built-in tools (Read, Write, Edit, Bash, Glob,
Grep). Handing those to a specialist would dissolve the write scoping the whole
design rests on: the Tester could patch `src/` with the built-in Write and make
its own report green. So the built-in surface is switched off (`tools=[]`) and
each specialist is handed exactly its allowlisted tools, re-exposed as an
in-process MCP server that forwards to the identical registry functions. The
same `guard()`, the same `resolve_for_write()`, the same artifact contract.
`WebSearch` is the single built-in kept, and only for specialists whose spec
already declares the `web_search` server tool.

`pick_backend("auto")` returns that backend or raises. There is deliberately no
path from it to a paid API key: `auth.probe()` refuses to start unless the
credential is positively identifiable as a subscription, and `auth.child_env()`
blanks every paid credential variable for the subprocess so a stray key in the
operator's shell cannot silently redirect the run onto per-token billing. The
paid `AnthropicBackend` survives for its hard-won resilience, but reaching it
takes two deliberate acts: `--backend anthropic` *and*
`HACKATHON_ALLOW_PAID_API=1`.

`routing.py` decides which model each specialist gets. On a subscription the
scarce resource is rate-limit windows -- and Opus has its own weekly window --
so the routing table is what stops slide assembly from consuming the budget the
architecture decision needs. `ledger.py` fingerprints each task over its spec,
tier, objective, context and the content of its required inputs, and replays
completed work instead of re-running it.
