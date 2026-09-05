"""Research specialists.

Shared shape: they can search and read the web, they must log provenance, and
they cannot touch code, tests, or any deliverable outside RESEARCH/. A
researcher that wants to change an implementation has to hand off.
"""

from __future__ import annotations

from ..base import AgentSpec, FileContains, HasHeadings, MinWords

RESEARCH_TOOLS = (
    "web_search", "fetch_url", "record_source", "list_sources",
    "read_file", "write_file", "knowledge_search",
)
LEDGER = "RESEARCH/sources.json"

MARKET_RESEARCHER = AgentSpec(
    name="market_researcher",
    title="Market Researcher",
    team="research",
    mission="""Size the opportunity this product addresses and characterise the market it
enters. Cover market size and growth, who the buyers are, what they pay today,
the business models that work in this space, and what actually blocks adoption.

Be specific and numerate. A market section that says "the market is large and
growing" is worthless to a judge. Record every figure with record_source; if a
number is your own estimate rather than something you found, record it as an
estimate and label it as such in the report. An honest estimate is credible; an
unmarked one destroys the whole report's credibility when a judge probes it.""",
    tools=RESEARCH_TOOLS,
    write_paths=("RESEARCH/market_report.md", LEDGER),
    requires=("AGENT/problem_statement.md",),
    produces=("RESEARCH/market_report.md",),
    postconditions=(
        FileContains(
            "RESEARCH/market_report.md",
            ("market size", "customer", "business model", "adoption"),
            label="Market report",
        ),
        HasHeadings("RESEARCH/market_report.md", 5),
        MinWords("RESEARCH/market_report.md", 300),
    ),
    context_keys=("problem", "constraints", "judging"),
)

COMPETITOR_RESEARCHER = AgentSpec(
    name="competitor_researcher",
    title="Competitor Researcher",
    team="research",
    mission="""Identify who already solves this problem and where they fall short.

Produce a comparison table: competitor, what they do, pricing, and the specific
weakness we can exploit. Include the honest answer to "why hasn't an incumbent
just done this?" -- judges ask it, and a team without an answer looks naive.

Do not dismiss competitors. A credible differentiation argument acknowledges
that the incumbent is good at something and explains what it structurally
cannot do. Record sources for every claim about a competitor's capabilities.""",
    tools=RESEARCH_TOOLS,
    write_paths=("RESEARCH/competitive_analysis.md", LEDGER),
    requires=("AGENT/problem_statement.md",),
    produces=("RESEARCH/competitive_analysis.md",),
    postconditions=(
        FileContains(
            "RESEARCH/competitive_analysis.md",
            ("competitor", "pricing", "weakness", "differentiation"),
            label="Competitive analysis",
        ),
        HasHeadings("RESEARCH/competitive_analysis.md", 4),
        MinWords("RESEARCH/competitive_analysis.md", 250),
    ),
    context_keys=("problem", "constraints", "judging"),
)

TECHNICAL_RESEARCHER = AgentSpec(
    name="technical_researcher",
    title="Technical Researcher",
    team="research",
    mission="""Find the shortest credible technical path to a working system.

Survey the APIs, frameworks, models, datasets and open-source projects that
could be used, and recommend a stack. Judge every option against hackathon
reality: can it be integrated in hours, does it need credentials we lack, does
it need training data we do not have, will it demo reliably offline.

Explicitly name approaches you rejected and why. A rejected-options list is the
most useful thing you can hand the Architect. Where a benchmark or a published
result informs the choice, record the source.""",
    tools=RESEARCH_TOOLS + ("search_code",),
    write_paths=("RESEARCH/technical_research.md", LEDGER),
    requires=("AGENT/problem_statement.md",),
    produces=("RESEARCH/technical_research.md",),
    postconditions=(
        FileContains(
            "RESEARCH/technical_research.md",
            ("recommended", "rejected", "risk", "dataset"),
            label="Technical research",
        ),
        HasHeadings("RESEARCH/technical_research.md", 5),
        MinWords("RESEARCH/technical_research.md", 300),
    ),
    context_keys=("problem", "constraints", "judging", "prior_art"),
)

USER_RESEARCHER = AgentSpec(
    name="user_researcher",
    title="User Researcher",
    team="research",
    mission="""Establish who this is for and what their day actually looks like.

Define the primary user precisely -- a role, not a demographic. Map the workflow
they use today, where it hurts, and the job they are hiring this product to do.
Produce a user journey the UX Designer can build against, and name the adoption
concerns that would stop this user from switching.

If the problem statement names a user, do not silently substitute a different
one because it is easier to build for.""",
    tools=RESEARCH_TOOLS,
    write_paths=("RESEARCH/user_research.md", LEDGER),
    requires=("AGENT/problem_statement.md",),
    produces=("RESEARCH/user_research.md",),
    postconditions=(
        FileContains(
            "RESEARCH/user_research.md",
            ("target user", "workflow", "pain point", "journey", "adoption"),
            label="User research",
        ),
        HasHeadings("RESEARCH/user_research.md", 5),
        MinWords("RESEARCH/user_research.md", 250),
    ),
    context_keys=("problem", "constraints"),
)

SPECS = [MARKET_RESEARCHER, COMPETITOR_RESEARCHER, TECHNICAL_RESEARCHER, USER_RESEARCHER]
