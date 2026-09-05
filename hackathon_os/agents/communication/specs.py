"""Communication specialists.

The pitch roles get `verify_claims` and the provenance ledger and nothing that
can change the product. They describe what exists; if the pitch needs a
stronger fact, the answer is more evidence, not better adjectives.
"""

from __future__ import annotations

from ..base import AgentSpec, Custom, FileContains, HasHeadings, MinWords

TECHNICAL_WRITER = AgentSpec(
    name="technical_writer",
    title="Technical Writer",
    team="communication",
    mission="""Document what was actually built. Read the code; do not paraphrase the plan.

Produce a README that gets a judge from clone to running in under five minutes,
plus technical and API documentation reflecting the real implementation.

Verify every command you write by reading the code that backs it. A README with
a setup step that does not work is worse than no README -- it makes a judge
think the whole project is careless. If a feature was planned and not built, it
does not appear in the documentation as though it exists.

State the actual prerequisites: Python version, dependencies, environment
variables, and anything the project needs that a clean machine will not have.

The README is also the repository's front page, so write it as one: title,
problem, solution, architecture, features, tech stack, setup, installation,
usage, demo, API surface, limitations, future work, team, hackathon and
licence. Two rules govern the whole document. Every section describes what
exists -- a "features" list containing things nobody built is the fastest way
to lose a judge's trust. And where something genuinely is not there, say so in
"limitations" rather than omitting the section; an honest gap reads as
engineering judgement, a silent one reads as an oversight.""",
    tools=(
        "read_file", "write_file", "search_code", "list_files",
        "run_shell", "git_status", "read_decisions",
    ),
    write_paths=("README.md", "DOCUMENTATION/", "AGENT/"),
    requires=("PRODUCT/architecture.md",),
    produces=("README.md", "DOCUMENTATION/technical.md"),
    postconditions=(
        FileContains(
            "README.md",
            ("problem", "solution", "architecture", "features", "tech stack",
             "setup", "install", "usage", "demo", "api", "limitations",
             "future work", "license"),
            label="README",
        ),
        MinWords("README.md", 250),
        HasHeadings("README.md", 8),
        HasHeadings("DOCUMENTATION/technical.md", 3),
    ),
    context_keys=("problem", "architecture", "product_plan", "test_results"),
)

PITCH_STRATEGIST = AgentSpec(
    name="pitch_strategist",
    title="Pitch Strategist",
    team="communication",
    mission="""Decide what the pitch argues, and against which criteria.

Work criterion by criterion: for each item in the judging rubric, name the
specific evidence this project offers and how strong it honestly is. Where the
evidence is thin, say so and propose the cheapest thing that would strengthen
it -- that proposal is often the most valuable output of this whole role.

Then structure the narrative: the problem, the insight that makes this
different, the solution, the proof, the impact. Lead with the insight, not the
technology. Judges remember one sentence; decide now what it is.

Every claim must trace to research or to something the project actually does.
Run verify_claims on your output before finishing. An unsourced number that a
judge questions costs more than the claim was ever worth.""",
    tools=(
        "read_file", "write_file", "list_files", "search_code",
        "list_sources", "verify_claims", "record_source", "read_decisions",
    ),
    write_paths=("PRESENTATION/pitch_strategy.md", "RESEARCH/sources.json", "AGENT/"),
    requires=("PRODUCT/strategy.md",),
    produces=("PRESENTATION/pitch_strategy.md",),
    postconditions=(
        FileContains(
            "PRESENTATION/pitch_strategy.md",
            ("judging criteri", "evidence", "differentiation", "narrative", "impact"),
            label="Pitch strategy",
        ),
        HasHeadings("PRESENTATION/pitch_strategy.md", 5),
        MinWords("PRESENTATION/pitch_strategy.md", 250),
    ),
    context_keys=("problem", "judging", "strategy", "research", "test_results"),
)


def _slides_have_enough(root):
    p = root / "PRESENTATION/slides.md"
    if not p.is_file():
        return "PRESENTATION/slides.md missing"
    n = sum(1 for line in p.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("## "))
    if n < 6:
        return f"slides.md has {n} slides, expected at least 6"
    return None


PRESENTATION_BUILDER = AgentSpec(
    name="presentation_builder",
    title="Presentation Builder",
    team="communication",
    mission="""Turn the pitch strategy into the deck, then render it.

Write PRESENTATION/slides.md first: `## Slide Title`, `- bullet` lines, and
`Notes:` for speaker notes. Then call build_pptx to render a real .pptx.

Rules that decide whether a deck lands: one idea per slide, at most six bullets,
no bullet longer than a line. Put the numbers on the slide and the caveats in
the speaker notes. Include a results slide with real measured figures and a
baseline to compare against -- a score with nothing to compare it to tells a
judge nothing.

Invent nothing. Every claim comes from research or from the project. If the
strategy asked for a figure that does not exist, leave a visible TODO rather
than inventing one, and flag it as a finding.""",
    tools=(
        "read_file", "write_file", "list_files", "build_pptx", "build_pdf",
        "list_sources", "verify_claims",
    ),
    write_paths=("PRESENTATION/", "AGENT/"),
    requires=("PRESENTATION/pitch_strategy.md",),
    produces=("PRESENTATION/slides.md", "PRESENTATION/presentation.pptx"),
    postconditions=(Custom(_slides_have_enough, name="slide_count"),),
    context_keys=("problem", "judging", "strategy", "test_results"),
    min_artifact_bytes=300,
)

SPECS = [TECHNICAL_WRITER, PITCH_STRATEGIST, PRESENTATION_BUILDER]
