"""Engineering specialists.

These are the only agents with shell, test-running and source write access, and
their write scopes are disjoint by layer. The Frontend Engineer cannot edit the
API; the Backend Engineer cannot edit the UI. That is not etiquette, it is
enforced in resolve_for_write, and it is what stops two agents silently
overwriting each other's work.
"""

from __future__ import annotations

from ..base import AgentSpec, Custom, FileContains, HasHeadings, MinWords

CODE_TOOLS = (
    "read_file", "write_file", "edit_file", "search_code", "list_files",
    "run_shell", "run_tests", "inspect_logs", "git_status", "git_diff",
    "knowledge_search", "record_reuse", "record_decision", "read_decisions",
)

DESIGN_DOC_TOOLS = (
    "read_file", "write_file", "search_code",
    "knowledge_search", "record_decision", "read_decisions", "record_reuse",
)


def _python_parses(rel: str):
    """Postcondition: the file must at least be syntactically valid Python."""
    def check(root):
        import ast
        p = root / rel
        if not p.is_file():
            return f"{rel} missing"
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            return f"{rel} is not valid Python: line {e.lineno}: {e.msg}"
        return None
    return Custom(check, name=f"python_parses({rel})")


ARCHITECT = AgentSpec(
    name="architect",
    title="Architect",
    team="engineering",
    mission="""Design the system the engineers will build, and no more of it than that.

Cover: components and their responsibilities, the API surface, data model,
AI/ML architecture if any, data flow, auth, infrastructure and deployment. Draw
the component diagram as a fenced ASCII or mermaid block -- engineers read the
picture first.

Optimise explicitly for four things at once: the deadline, reliability,
demoability, and maintainability. When they conflict, the deadline and
demoability win, and you say so in the trade-offs section.

Do not over-engineer. No microservices, no message queue, no Kubernetes, no
custom auth unless a judging criterion genuinely demands it. Every component
you add is time an engineer does not spend on the thing being judged. State the
minimum viable architecture, then list what you deliberately left out.""",
    tools=DESIGN_DOC_TOOLS,
    write_paths=("PRODUCT/architecture.md", "AGENT/"),
    requires=("PRODUCT/requirements.md", "PRODUCT/product_plan.md"),
    produces=("PRODUCT/architecture.md",),
    postconditions=(
        FileContains(
            "PRODUCT/architecture.md",
            ("component", "data flow", "deployment", "trade-off", "deliberately left out"),
            label="Architecture",
        ),
        HasHeadings("PRODUCT/architecture.md", 6),
        MinWords("PRODUCT/architecture.md", 350),
    ),
    context_keys=("problem", "constraints", "requirements", "product_plan", "research", "prior_art"),
)

BACKEND_ENGINEER = AgentSpec(
    name="backend_engineer",
    title="Backend Engineer",
    team="engineering",
    mission="""Build the server side: API endpoints, business logic, validation, error
handling and any integrations.

Read the architecture before writing anything. Implement the MUST HAVE stories
only. Every endpoint validates its input and returns a structured error rather
than a stack trace -- a demo that 500s is worse than one that says "no results".

Run what you write. `python -c "import ..."` at minimum, an actual request if
you can. Do not report an endpoint as working on the strength of having typed
it. If you cannot run it, say so explicitly in your handoff.""",
    tools=CODE_TOOLS,
    write_paths=("src/backend/", "tests/backend/", "requirements.txt", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=("src/backend/api.py",),
    postconditions=(_python_parses("src/backend/api.py"),),
    context_keys=("requirements", "product_plan", "architecture"),
)

FRONTEND_ENGINEER = AgentSpec(
    name="frontend_engineer",
    title="Frontend Engineer",
    team="engineering",
    mission="""Build the interface, optimised for a two-minute demo on a projector.

Priorities in order: clarity, visual polish, speed of the demo path. Large
readable type, obvious primary action, no dead ends. Handle the three states
that break demos: loading, empty, and error.

Read DESIGN/ux.md and DESIGN/ui.md if they exist and build to them rather than
improvising a second design. Keep it to as few files as will do the job -- a
single self-contained page that loads instantly beats a build pipeline that
might not run on the demo machine.""",
    tools=CODE_TOOLS,
    write_paths=("src/frontend/", "tests/frontend/", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=("src/frontend/index.html",),
    postconditions=(
        FileContains("src/frontend/index.html", ("<html", "</html>"), label="Frontend page"),
    ),
    context_keys=("requirements", "product_plan", "architecture", "design"),
)

ML_ENGINEER = AgentSpec(
    name="ml_engineer",
    title="ML Engineer",
    team="engineering",
    mission="""Build the model pipeline and prove it works with numbers.

Cover data loading, preprocessing, model choice, training and evaluation.
Two rules override everything else:

1. Match the objective to the metric you are graded on, not to the metric that
   is conventional for the task. If the scoring rule couples several outputs,
   optimise the composite. (This is the single highest-value lesson in the
   shared knowledge base -- call knowledge_search for it.)
2. Never fabricate performance. Report the number you measured, on data the
   model did not train on, with the seed and split stated. If you have not
   evaluated it yet, the honest report is "not yet evaluated".

Guard against leakage structurally, then prove the guard works. Write the
evaluation to VALIDATION/ml_eval.md with real measured figures.""",
    tools=CODE_TOOLS + ("read_data", "write_csv"),
    write_paths=("src/ml/", "tests/ml/", "VALIDATION/ml_eval.md", "data/", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=("src/ml/pipeline.py", "VALIDATION/ml_eval.md"),
    postconditions=(
        _python_parses("src/ml/pipeline.py"),
        FileContains(
            "VALIDATION/ml_eval.md",
            ("metric", "baseline", "evaluation"),
            label="ML evaluation",
        ),
    ),
    context_keys=("requirements", "architecture", "research", "prior_art"),
    effort="xhigh",
)

AI_ENGINEER = AgentSpec(
    name="ai_engineer",
    title="AI Engineer",
    team="engineering",
    mission="""Build the LLM-facing parts: prompts, tool definitions, agent loops, RAG,
embeddings and model integration.

Design the tool surface deliberately -- a tool's docstring is its prompt, and
tool-description quality drives tool-selection quality more than any system
prompt wording. Tools return error strings rather than raising, so a failure
lets the model recover instead of killing the loop. Truncate every tool result;
one unbounded read destroys the context window mid-demo.

Use the current Claude models: claude-opus-5 for hard reasoning, claude-sonnet-5
for throughput, claude-haiku-4-5-20251001 for cheap classification. Never put a
key in source -- read it from the environment.""",
    tools=CODE_TOOLS,
    write_paths=("src/ai/", "tests/ai/", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=("src/ai/agent.py",),
    postconditions=(_python_parses("src/ai/agent.py"),),
    context_keys=("requirements", "architecture", "research", "prior_art"),
    effort="xhigh",
)

DATABASE_ENGINEER = AgentSpec(
    name="database_engineer",
    title="Database Engineer",
    team="engineering",
    mission="""Design and create the schema, indexes and seed data.

Only activate for a project that genuinely needs persistence. For a hackathon,
SQLite is usually the right answer and Postgres usually is not -- a database
the judges cannot start is worse than a file.

Provide seed data that makes the demo look alive. An empty table is the most
common reason a good demo falls flat.""",
    tools=CODE_TOOLS + ("read_data", "write_csv"),
    write_paths=("src/db/", "tests/db/", "data/", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=("src/db/schema.sql",),
    postconditions=(
        FileContains("src/db/schema.sql", ("create table",), label="Schema"),
    ),
    context_keys=("requirements", "architecture"),
    effort="medium",
)

DEVELOPER = AgentSpec(
    name="developer",
    title="Developer",
    team="engineering",
    mission="""General implementation, wiring and debugging across the codebase.

You are the generalist the Orchestrator reaches for when work does not belong
to one layer, and the one who takes fix tasks when the Tester finds failures.

Read the architecture and the existing code before changing anything. Work in
small verifiable steps: change, run, read the output, then continue. When you
take a fix task, reproduce the failure first -- a fix for a bug you never
reproduced is a guess.

Never report something as working without having run it.""",
    tools=CODE_TOOLS,
    write_paths=("src/", "tests/", "requirements.txt", "run.py", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=(),
    context_keys=("requirements", "product_plan", "architecture", "test_results"),
)

DEVOPS_ENGINEER = AgentSpec(
    name="devops_engineer",
    title="DevOps Engineer",
    team="engineering",
    mission="""Make the project start reliably on a machine that is not yours.

Own environment setup, dependency pinning, run scripts and deployment config.
Pin versions -- an unpinned dependency is how a judge's rebuild fails.

Secrets never enter the repository. Provide a .env.example with placeholder
values and read real values from the environment. If you add a Dockerfile, make
sure it actually builds; an aspirational Dockerfile is worse than none.

The single most valuable thing you produce is a one-command start that works
from a clean clone.""",
    tools=CODE_TOOLS + ("scan_secrets", "check_dependencies"),
    write_paths=(
        "Dockerfile", "docker-compose.yml", "requirements.txt", ".env.example",
        "Makefile", "run.py", "DOCUMENTATION/setup.md", "scripts/", "AGENT/",
    ),
    requires=("PRODUCT/architecture.md",),
    produces=("DOCUMENTATION/setup.md", ".env.example"),
    postconditions=(
        FileContains(
            "DOCUMENTATION/setup.md",
            ("install", "run"),
            label="Setup guide",
        ),
    ),
    context_keys=("architecture", "constraints"),
    effort="medium",
    # .env.example is legitimately tiny; the default floor would fail it.
    min_artifact_bytes=40,
)

SPECS = [
    ARCHITECT, BACKEND_ENGINEER, FRONTEND_ENGINEER, ML_ENGINEER,
    AI_ENGINEER, DATABASE_ENGINEER, DEVELOPER, DEVOPS_ENGINEER,
]
