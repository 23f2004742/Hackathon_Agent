Hackathon Agent OS

«From problem statement to submission — autonomously.»

An autonomous, Claude-powered multi-agent system for building hackathon projects end-to-end.

Hackathon Agent OS takes a hackathon problem statement and coordinates specialized agents across research, product, engineering, design, validation, communication, and delivery to turn the idea into a tested, documented, demo-ready, submission-ready project.

---

✨ What It Does

Instead of manually deciding:

- What should we build?
- What technologies should we use?
- Which research is necessary?
- Which engineers do we need?
- What should each agent work on?
- Which model should handle each task?
- How do we test everything?
- How do we prepare the submission?
- What files are safe to push to GitHub?

Hackathon Agent OS handles the workflow automatically.

                    PROBLEM STATEMENT
                           │
                           ▼
                  ┌──────────────────┐
                  │ Project Analysis  │
                  └────────┬─────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │ Intelligent Agent Planner│
              └────────────┬────────────┘
                           │
                           ▼
                    Specialist Selection
                           │
                           ▼
                       Task Graph
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
      Token Optimizer              Model Planner
             │                           │
             └─────────────┬─────────────┘
                           ▼
                 Specialist Execution
                           │
                           ▼
                       Validation
                           │
                           ▼
                     Re-planning
                           │
                           ▼
                  Documentation / Demo
                           │
                           ▼
                     Final Audit
                           │
                           ▼
                       Packaging
                           │
                           ▼
                    GitHub Preparation
                           │
                           ▼
                       Submission

---

🧠 Architecture

Hackathon Agent OS is built around a multi-agent orchestration architecture.

The central orchestrator does not perform the work itself. It decomposes the project into dependency-aware tasks and delegates them to specialized agents.

Each specialist has:

- a defined mission
- specific tools
- required inputs
- expected outputs
- artifact contracts
- permitted write paths
- dependencies
- model/effort configuration

This prevents every agent from having unrestricted access to the entire system.

---

👥 Specialist Teams

The system currently contains 28 specialist roles across seven teams.

Research

- "market_researcher"
- "competitor_researcher"
- "technical_researcher"
- "user_researcher"

Product

- "requirements_analyst"
- "product_manager"
- "strategist"

Engineering

- "architect"
- "backend_engineer"
- "frontend_engineer"
- "ml_engineer"
- "ai_engineer"
- "database_engineer"
- "developer"
- "devops_engineer"

Design

- "ux_designer"
- "ui_designer"
- "brand_designer"

Validation

- "tester"
- "code_reviewer"
- "security_reviewer"
- "requirements_auditor"

Communication

- "technical_writer"
- "pitch_strategist"
- "presentation_builder"

Delivery

- "demo_engineer"
- "final_auditor"
- "submission_manager"

The system does not automatically run every specialist.

---

🎯 Intelligent Specialist Selection

The Agent OS determines which specialists are actually required for a particular hackathon.

Selection happens in multiple stages.

Problem Statement
       ↓
Deterministic Capability Detection
       ↓
Claude Capability Planner
       ↓
Dependency / Coherence Validation
       ↓
Final Specialist Set

The planner can identify capabilities such as:

- AI
- ML
- LLM
- RAG
- NLP
- Computer Vision
- frontend
- backend
- database
- APIs
- cloud
- DevOps
- security
- blockchain
- payments
- IoT
- hardware
- research
- UX/UI
- data analysis

For example, a simple ML notebook should not automatically receive frontend, backend, UI, database, and DevOps specialists.

The system also records why a specialist was skipped.

Example:

frontend_engineer
Status: SKIPPED

Reason:
The project is a CLI/data-analysis submission and does not require a web interface.

---

🔥 Token & Context Optimization

Hackathon Agent OS includes a dedicated token optimization layer.

The goal is to avoid wasting Claude usage by repeatedly sending unnecessary context.

Instead of sending the entire repository to every agent, the system provides targeted context.

Optimization includes

- context prioritization
- context deduplication
- artifact compression
- targeted file excerpts
- dependency-aware context retrieval
- knowledge-base reuse
- result caching
- token budgeting
- context-size monitoring

Context priority generally follows:

Current Task
     ↓
Direct Dependencies
     ↓
Relevant Artifacts
     ↓
Project State
     ↓
Relevant Knowledge
     ↓
Historical References
     ↓
General Background

Agents can use their filesystem tools to inspect additional information when necessary.

The system also records optimization metrics such as:

estimated_input_tokens
estimated_output_tokens
context_tokens_removed
cache_hits
cache_misses
compression_ratio

---

🤖 Dynamic Model Planning

The Agent OS does not permanently force expensive models onto specific roles.

Model selection is task-aware.

Role
 ↓
Task
 ↓
Complexity / Risk / Importance
 ↓
Model Planner
 ↓
Routing Constraints
 ↓
Final Model

The configured default model is used whenever it is sufficient.

More capable models can be selected when a task genuinely requires additional reasoning.

Typical examples:

Simple

- file organization
- formatting
- straightforward documentation
- basic extraction

→ Default model

Medium

- requirements analysis
- normal implementation
- UI work
- research synthesis
- test generation

→ Default unless escalation is justified

Complex

- difficult architecture
- complex debugging
- security analysis
- advanced AI/ML reasoning
- high-risk final review

→ Stronger model when justified

The model planner records its decision:

{
  "task": "security_review",
  "model": "opus",
  "reason": "High-risk security analysis with multiple interacting components",
  "confidence": 0.91
}

Model decisions are persisted and can be inspected.

Explicit model overrides are also supported.

---

🔄 Persistent State & Resume

Long hackathon runs can exceed a single Claude usage window.

The system therefore persists:

- task states
- specialist selection
- planner decisions
- model decisions
- artifacts
- handoffs
- failures
- retries
- checkpoints
- token optimization information
- packaging state

A run can be resumed without restarting completed tasks.

python hackathon.py resume

The system reconstructs project context from persistent state, artifacts, handoffs, and the knowledge layer.

---

🧩 Task Graph

Tasks are represented as a dependency graph rather than a simple linear pipeline.

Example:

Requirements
     │
     ├──────────────┐
     ▼              ▼
Product         Architecture
                    │
          ┌─────────┼─────────┐
          ▼         ▼         ▼
       Backend   Frontend      AI
          │         │          │
          └─────────┼──────────┘
                    ▼
                  Testing
                    │
                    ▼
               Final Audit
                    │
                    ▼
                 Package

Independent tasks can execute in waves.

Failed or blocked tasks can propagate through the dependency graph without forcing the entire project to restart.

---

📚 Cross-Hackathon Knowledge

The parent ".knowledge/" directory acts as a persistent knowledge base.

It can contain reusable:

- engineering patterns
- architecture decisions
- research
- prompts
- UI patterns
- lessons learned
- implementation techniques
- hackathon-specific insights

Previous hackathons can be used as references for new projects.

Historical projects are treated as references and should not be modified automatically.

---

🛠️ Tools

Agents receive only the tools required for their role.

The tool system includes capabilities for:

- filesystem operations
- code search
- shell execution
- web research
- URL fetching
- source recording
- knowledge retrieval
- project management
- security scanning
- document generation
- structured handoffs

The system also supports the Claude Agent SDK's web search capability where configured.

---

📦 Packaging

The Agent OS can create a clean hackathon submission package.

python hackathon.py package

The package process can prepare:

- source code
- README
- requirements
- documentation
- demo materials
- presentation assets
- configuration templates

Generated/runtime files are excluded.

Typical exclusions include:

.env
.venv
__pycache__
.pytest_cache
node_modules
.git
temporary files
runtime state
credentials
private keys

---

🔐 Security & Secret Scanning

Before packaging or GitHub preparation, the system checks for potentially sensitive information.

It can detect things such as:

- API keys
- access tokens
- passwords
- private keys
- ".env" files
- cloud credentials
- database credentials

A package should not be considered GitHub-ready until the secret scan passes.

Example:

SECRET SCAN
-----------

✓ No secrets detected
✓ No credential files detected
✓ .env excluded

---

🐙 GitHub Workflow

The system separates building a package from actually pushing it.

Initialize GitHub repository

python hackathon.py github init

This prepares:

- Git repository
- ".gitignore"
- README
- repository structure

Review commit contents

python hackathon.py github prepare

This shows:

- files to commit
- excluded files
- secret scan
- package size
- GitHub readiness

Push

python hackathon.py github push

Pushing requires explicit user action.

The Agent OS does not silently push code to GitHub.

---

📋 Planning

View the project plan with:

python hackathon.py plan

The planner can show:

PROJECT
-------
Type:
Complexity:
Capabilities:

SPECIALISTS
-----------
Selected:
Skipped:

TASK GRAPH
----------
Wave 1:
Wave 2:
Wave 3:
...

MODEL PLAN
----------
Default:
Sonnet:
Opus:

TOKEN PLAN
----------
Estimated context:
Estimated output:
Cache opportunities:

---

📊 Status

View current project state with:

python hackathon.py status

Example:

PROJECT STATUS
==============

Tasks
-----
Completed: 18
Running: 2
Pending: 5
Failed: 0
Skipped: 3

SPECIALISTS
-----------
Selected: 20 / 28

MODELS
------
Default: 14
Sonnet: 5
Opus: 1

TOKEN OPTIMIZATION
------------------
Cache hits: 17
Cache misses: 6
Context saved: ...

PACKAGE
-------
Status: Ready
Secret scan: PASS
GitHub: Ready

---

🚀 Quick Start

1. Clone

git clone <repository-url>
cd hackathon

2. Create a virtual environment

Windows

python -m venv .venv
.\.venv\Scripts\Activate.ps1

Linux / macOS

python3 -m venv .venv
source .venv/bin/activate

3. Install dependencies

python -m pip install -r requirements.txt

4. Authenticate Claude

The intended live backend uses Claude Agent SDK subscription authentication.

Authenticate through Claude as configured for your environment.

Then verify:

python hackathon.py auth

5. Create a project

Provide a hackathon project containing the required problem statement and submission information.

Then:

python hackathon.py --project <project-directory> plan

6. Run

python hackathon.py --project <project-directory> run

7. Resume

If execution is interrupted:

python hackathon.py --project <project-directory> resume

8. Package

python hackathon.py --project <project-directory> package

9. Prepare GitHub

python hackathon.py --project <project-directory> github init
python hackathon.py --project <project-directory> github prepare

10. Push

Only after reviewing the generated files:

python hackathon.py --project <project-directory> github push

---

🧪 Testing

The project includes a synthetic hackathon environment and automated tests.

Run:

python -m pytest -q

Current test status:

240 passed

The tests cover areas including:

- orchestration
- task graph
- state persistence
- specialist selection
- tools
- handoffs
- authentication
- packaging
- security
- model planning
- token optimization
- CLI behavior

A synthetic end-to-end hackathon can also be used to validate the orchestration pipeline without consuming a live Claude subscription run.

---

📁 Project Structure

hackathon/
│
├── hackathon.py
│
├── hackathon_os/
│   ├── orchestrator.py
│   ├── taskgraph.py
│   ├── state.py
│   ├── context.py
│   ├── ledger.py
│   ├── handoff.py
│   ├── routing.py
│   ├── model_planner.py
│   ├── token_optimizer.py
│   ├── packaging.py
│   ├── github.py
│   ├── subscription.py
│   ├── auth.py
│   ├── llm.py
│   │
│   ├── agents/
│   │   ├── research/
│   │   ├── product/
│   │   ├── engineering/
│   │   ├── design/
│   │   ├── validation/
│   │   ├── communication/
│   │   └── delivery/
│   │
│   └── tools/
│       ├── base.py
│       ├── filesystem.py
│       ├── shell.py
│       ├── research.py
│       ├── project.py
│       ├── security.py
│       ├── documents.py
│       └── handoff_tool.py
│
├── .knowledge/
│   ├── index.json
│   ├── index.md
│   └── patterns.md
│
├── synthetic_test/
│
├── tests/
│
├── AGENTS.md
├── ARCHITECTURE.md
├── BUILD_REPORT.md
├── requirements.txt
└── README.md

---

🔑 Design Principles

1. Specialization over generalization

Every specialist has a specific responsibility.

2. Least privilege

Agents only receive the tools and write access required for their task.

3. Dependency-aware execution

Tasks are executed according to their dependencies.

4. Minimal context

Agents receive the smallest useful context rather than the entire project.

5. Default to sufficient models

Use the configured default model unless stronger reasoning is justified.

6. Persistent execution

Interrupted runs should resume rather than restart.

7. Evidence over fabrication

Research, metrics, user studies, and benchmarks must never be fabricated.

8. Human-controlled delivery

The system can prepare a GitHub repository but does not silently push or submit work.

9. Reusable knowledge

Lessons and patterns can be reused across hackathons without modifying historical projects.

---

⚠️ Current Limitations

The architecture and automated test suite are validated, but live hackathon runs depend on:

- Claude Agent SDK availability
- Claude subscription authentication
- subscription usage limits
- model availability
- external web services
- the complexity of the target hackathon

A full live hackathon run may consume a significant portion of a Claude subscription usage window.

The synthetic test environment is therefore provided for deterministic end-to-end testing.

---

🗺️ Roadmap

Potential future improvements include:

- stronger automatic replanning
- improved long-term knowledge retrieval
- better token estimation
- richer GitHub integration
- automated issue generation
- pull-request workflows
- deeper codebase understanding
- parallel agent execution optimization
- improved failure recovery
- benchmark-driven model routing
- hackathon-specific strategy optimization

---

🤝 Contributing

Contributions are welcome.

When contributing:

1. Preserve specialist boundaries.
2. Keep tools least-privileged.
3. Add tests for new behavior.
4. Avoid hard-coded credentials.
5. Do not introduce unnecessary model usage.
6. Preserve resumability.
7. Keep generated artifacts out of Git.
8. Document architectural changes.

---

📜 License

Choose and add an appropriate open-source license before publishing the repository.

---

⭐ Philosophy

Hackathons are usually constrained by time, not ideas.

Hackathon Agent OS is designed to turn that constraint into an engineering pipeline:

Idea
 ↓
Research
 ↓
Strategy
 ↓
Product
 ↓
Architecture
 ↓
Implementation
 ↓
Design
 ↓
Testing
 ↓
Documentation
 ↓
Demo
 ↓
Pitch
 ↓
Audit
 ↓
Package
 ↓
Submission

One problem statement in.
One competition-ready project out.