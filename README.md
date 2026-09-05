# Hackathon Agent OS

An autonomous hackathon development environment. Give it a problem statement;
it selects a team of specialists, plans the work, builds, tests, documents,
pitches, audits and packages a submission.

28 specialists across 7 teams, 27 tools, coordinated by an Orchestrator through
a dependency-aware task graph.

## Setup

It runs on **your Claude subscription**. There is no API key, and no paid
request anywhere in the system.

```bash
# 1. install
pip install -r requirements.txt

# 2. log in with your existing Claude account (opens a browser, once)
claude

# 3. confirm — this makes no model request and costs no usage
python hackathon.py auth
```

`auth` should print `AUTHENTICATION OK` and name your plan. If it does not, it
prints the exact command that will fix it.

On a machine with no browser (CI, a server, a container), swap step 2 for a
long-lived subscription token:

```bash
claude setup-token                        # prints a token; requires Pro/Max/Team/Enterprise
export CLAUDE_CODE_OAUTH_TOKEN=<token>    # PowerShell: $env:CLAUDE_CODE_OAUTH_TOKEN = "<token>"
```

Then run a hackathon:

```bash
python hackathon.py init my-hackathon \
  --problem      brief/problem.md \
  --judging      brief/judging.md \
  --submission   brief/submission.md \
  --constraints  brief/constraints.md

python hackathon.py --project my-hackathon plan     # who's needed, and why
python hackathon.py --project my-hackathon run      # work until it cannot
python hackathon.py --project my-hackathon status   # the dashboard
```

`--backend simulated` exercises the same machinery with no model at all, so you
can inspect the pipeline for free — it produces structurally valid artifacts
that say, in their own first paragraph, that they are scaffolding.

### Authentication, billing, limits

| | |
|---|---|
| **Mechanism** | The [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) (`claude-agent-sdk`), which runs the Claude Code CLI and uses the credential you logged in with. |
| **Billing** | Your Claude subscription. Usage counts against your plan's windows; nothing is billed per token. |
| **API keys** | Never read. If `ANTHROPIC_API_KEY` (or a bearer token, or a Bedrock/Vertex/Foundry variable) is set in your shell, it is **blanked for the CLI subprocess** so a stray key cannot silently redirect the run onto paid billing. |
| **When the limit is hit** | The run stops. It does not fall back to an API key, does not enable usage credits, and does not retry into a closed window. Finished work is in the ledger — re-run the same command after the window resets and it resumes without repeating anything. |

The one thing to know: this is your own subscription driving your own machine.
Anthropic does not permit third-party products to offer *other people's*
claude.ai logins, so do not ship this as a hosted service that logs your users
in — for that you would need an API key and their approval.

The escape hatch, off by default and requiring two deliberate acts, is
`HACKATHON_ALLOW_PAID_API=1` plus `--backend anthropic`. Nothing selects it for
you.

---

## What makes these real agents

Not a set of system prompts. Each specialist is a bundle the runtime enforces:

```
tools        ── only these are sent to the model; guard() re-checks at call time
write_paths  ── writes outside scope return an error naming the real scope
requires     ── missing inputs → BLOCKED before a token is spent
produces     ── a "completed" claim with no files on disk becomes FAILED
postconditions ── 66 declarative checks: does the doc cover adoption, does the
                  manifest parse, does the generated Python compile
```

The Tester, for example, **cannot write to `src/`**. An agent that can patch
the code it is testing makes reports green instead of making products work.
Failures leave as proposed fix tasks for the Developer; only the Orchestrator
schedules them.

```bash
python -m pytest tests/test_boundaries.py -v   # the boundaries, asserted
python -m pytest tests -q                      # 240 tests
```

---

## Commands

| | |
|---|---|
| `init` | Create a project from a brief |
| `plan` | Analyse, select specialists, build the graph, assign models and token budgets |
| `select <brief>` | Preview selection for any brief without creating a project (`--planner` for the Claude pass) |
| `run` | Work autonomously (`--once` one wave, `--task <id>` one task, `--dry-run`) |
| `resume` | Re-queue failures and continue; completed work replays from the ledger |
| `status` | The dashboard: tasks, specialists, models, token optimisation, package readiness |
| `package` | Build a clean submission package (`--dry-run` to preview, `--force` to override a scan) |
| `github init\|prepare\|push` | Prepare a GitHub-ready repository; `push` requires explicit confirmation |
| `models` | The model catalogue and the policy that picks between them |
| `tasks` / `graph` | Task table / dependency tree |
| `handoffs` | What each specialist reported (`--json`) |
| `agents [name]` | The roster, or one specialist's full contract |
| `tools` | The tool registry by category |
| `auth` | Which credential a run would use, and how to fix it (no model request) |
| `routing` | The effort tier each specialist runs at, and why |
| `research` `scope` `design` `build` `test` `demo` `docs` `pitch` `audit` `submit` | Run one stage only |

Global flags come **before** the subcommand: `--project`, `--backend`,
`--parallel`, `--approve` (prompt on every write/shell call), `--no-cache`
(re-run tasks the ledger already has), `--model` (force one model for the whole
run), `--no-optimize` (send raw context digests), `--no-planner` (deterministic
selection only), `--quiet`.

> The stage verb that runs the Submission Manager is `submit`. `package` is now
> the packaging command, which is what someone typing that word expects.

---

## Not every project gets all 28

Selection is two stages. First, negation-aware capability detection over the
full brief — problem, constraints, judging criteria, submission requirements:

```bash
$ python hackathon.py select "Static site for a food bank. No backend, no database."
# frontend + ux + ui; no backend, no database, no ml

$ python hackathon.py select "Tabular leaderboard task, scored by RMSE. No interface required."
# ml_engineer; no frontend, no designers, no backend
```

Then `plan` asks Claude, in one toolless call carrying only the brief and the
roster, which specialists this problem actually needs. It can add roles the
regexes missed and remove ones they guessed at — but it cannot drop the
mandatory delivery spine, cannot overrule a capability the brief states
outright, and cannot invent a specialist that is not in the roster.

Both halves are recorded. Every skip names the capability that would have
selected it:

```
SKIPPED (1)
-------
  ○ ml_engineer   no ml or vision signal in the brief; say so explicitly if
                  the project needs it
```

If a specialist later discovers the project *does* need something nobody
staffed, the Orchestrator adds those specialists and their tasks and carries on.
It does not restart the hackathon.

---

## Layout

```
hackathon/
├── hackathon_os/          the OS
├── .knowledge/            cross-hackathon lessons and components (shared)
├── AGENT/                 this build's own decision and reference logs
├── tests/                 240 tests
├── synthetic_test/        a completed end-to-end run you can inspect
├── AGENTS.md              every specialist: tools, scope, contract, handoff
├── ARCHITECTURE.md        how it works
└── BUILD_REPORT.md        what was built, what was tested, what was not
```

A project directory gets `RESEARCH/ PRODUCT/ DESIGN/ VALIDATION/ DEMO/
DOCUMENTATION/ PRESENTATION/ SUBMISSION/ FINAL/ src/ tests/ AGENT/` — and the
specialist write scopes are defined against exactly that shape. `hackathon package` builds `dist/submission/` from it.

---

## Three memory layers

| Layer | Where | What |
|---|---|---|
| Global | `.knowledge/` | Lessons and reusable components across every hackathon. Read via `knowledge_search`. |
| Project | `<project>/AGENT/` | State, plan, decision log, reference decisions |
| Task | assembled per run | Only the context slices a spec declares — digests, then prioritised, deduplicated, compressed and trimmed to a per-task budget |

This is why a Brand Designer never receives the test report, and why the same constraint appearing in three places is sent once.

---

## Spending the budget where it matters

On a subscription the currency is not dollars, it is rate-limit windows — and
Opus has its own weekly window, separate from the rest.

**The model is chosen per task, not per role.** The default (Sonnet) is presumed
sufficient; anything stronger has to be argued for. A task scores on reasoning
weight, priority, effort, project complexity, previous attempts and domain risk,
and only a high score buys an upgrade. Mechanical work — markdown to slides,
files to a checklist — drops *below* the default, because there is no judgement
to buy. A typical run lands around 70% default, 20% upgraded, 10% below.

Every decision carries its reason and is shown by `plan`:

```
architecture   opus     complexity 7/10 justifies an upgrade: reasoning weight 3,
                        critical priority, high effort   (confidence 0.65)
slides         haiku    mechanical transform with no judgement to buy  (0.90)
docs           sonnet   standard task (complexity 4/10); default is sufficient
```

Once a task starts on a model it stays there. A failure escalates it one step,
once, and the escalation is recorded. `--model opus` overrides everything
deliberately; `python hackathon.py models` prints the catalogue and the policy;
`HACKATHON_DEFAULT_MODEL` and `HACKATHON_MODELS` reconfigure it without a code
change, because model ids go stale.

`routing.py` still sets effort and turn limits per role —
`python hackathon.py routing` prints that table.

**Context is optimised before it is sent.** Candidate slices are ranked into
seven priority bands, deduplicated across sources (the same constraint appears
in the state, a handoff and an artifact digest — it is sent once), compressed if
oversized, and trimmed to a per-task budget. Compression keeps the decisions,
interfaces, endpoints, constraints, paths, errors and acceptance criteria and
drops the prose between them; it never truncates, because the acceptance
criteria live at the *bottom* of the document. What was abridged is labelled, so
the agent knows to call `read_file`.

`status` reports what that saved:

```
TOKEN OPTIMIZATION
  Estimated input   40,640 tokens sent
  Context saved     4,436 tokens (10% of candidate context)
  Compression ratio 0.902
  Cache hits        0
  Cache misses      27
  deduplicated 120 block(s), compressed 0, dropped 0 low-priority slice(s)
```

Three further savings:

- **Task deduplication.** Every task is fingerprinted over its spec, model,
  objective, context and the *content* of its required inputs. An identical task
  whose artifacts are still on disk unmodified is replayed from
  `AGENT/cache/ledger.json` with no model call. This is what makes `run`
  resumable after a crash or a usage limit.
- **The artifact contract is in the prompt.** A specialist is told the checks
  its files must pass before it writes them, rather than discovering them by
  failing — which used to cost a whole extra run.
- **No inherited context.** `setting_sources=[]`: a specialist gets the context
  slices its spec declares and nothing else — not your `CLAUDE.md`, settings or
  skills.

---

## Shipping it

```bash
python hackathon.py package          # dist/submission/, secret-scanned
python hackathon.py github init      # git init, .gitignore, README, validate
python hackathon.py github prepare   # exactly what would be committed
python hackathon.py github push --yes
```

Packaging **fails** if a live-looking secret is anywhere in the working tree —
not just in the files it would copy, because a `.env` you excluded from the
archive is still sitting in the directory `git add -A` runs over:

```
BLOCKED
-------
Potential secret detected:
  .env
```

`prepare` asks git what it would commit rather than reconstructing it, so what
you read is what you get. `push` is the only outward-facing action in the whole
system: it refuses without explicit confirmation, refuses on a failed scan, and
stores no GitHub credential of its own — it uses the `git` and `gh` you already
have.

---

## Honest status

The orchestration machinery is tested end to end: **240 passing tests**, a
complete 27-task synthetic hackathon producing a real `.pptx`, a secret-free
package and an initialised repository.

Live runs against a real model work on a Claude subscription, and single
specialists have been verified end to end on it. A full 28-specialist live run
has not been completed, so **content quality across a whole hackathon is still
unproven** — the plumbing is what is tested. The Claude capability planner in
particular is exercised only through the simulated backend, which re-derives its
answer from the deterministic rules: the merge logic and guardrails are tested,
the judgement is not. `BUILD_REPORT.md` sets out exactly what that does and does
not demonstrate, and lists ten known limitations.

## Requirements

Python 3.11+. `claude-agent-sdk` and the Claude Code CLI (the SDK bundles a
binary on most platforms), plus `requests`, `python-pptx`, `reportlab`,
`openpyxl`, `pytest`. A Claude Pro, Max, Team or Enterprise plan. No API key.
