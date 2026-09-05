"""Product specialists.

These own scope. They have no shell and no code write access on purpose: the
job is deciding what gets built, and a product agent that can quietly start
building stops making that decision honestly.
"""

from __future__ import annotations

from ..base import AgentSpec, FileContains, HasHeadings, MinWords

PRODUCT_TOOLS = (
    "read_file", "write_file", "search_code",
    "knowledge_search", "record_decision", "read_decisions",
)

REQUIREMENTS_ANALYST = AgentSpec(
    name="requirements_analyst",
    title="Requirements Analyst",
    team="product",
    mission="""Turn the problem statement into an unambiguous, checkable contract.

Produce: functional requirements, non-functional requirements, constraints,
acceptance criteria, the judging criteria, and the submission requirements.
Every requirement gets an ID (FR-1, NFR-1, C-1) so later specialists can cite
it and the Requirements Auditor can check coverage mechanically.

Read the problem statement literally. Where it is ambiguous, do not resolve the
ambiguity silently -- write the interpretation down as an explicit assumption
and flag it as a finding. Where it states a hard constraint (a language, a
deadline, a submission format), that constraint overrides anything suggested by
a previous hackathon.

Acceptance criteria must be things someone could actually test, not aspirations.""",
    tools=PRODUCT_TOOLS,
    write_paths=("PRODUCT/requirements.md", "AGENT/"),
    requires=("AGENT/problem_statement.md",),
    produces=("PRODUCT/requirements.md",),
    postconditions=(
        FileContains(
            "PRODUCT/requirements.md",
            ("functional requirement", "non-functional", "constraint",
             "acceptance criteria", "judging criteria", "submission"),
            label="Requirements",
        ),
        HasHeadings("PRODUCT/requirements.md", 6),
        MinWords("PRODUCT/requirements.md", 300),
    ),
    context_keys=("problem", "constraints", "judging", "submission"),
    effort="high",
)

PRODUCT_MANAGER = AgentSpec(
    name="product_manager",
    title="Product Manager",
    team="product",
    mission="""Decide what actually gets built in the time available, and what does not.

Produce a MoSCoW breakdown with four populated sections: MUST HAVE, SHOULD
HAVE, NICE TO HAVE, and DO NOT BUILD. The last one is the most valuable thing
you produce -- an explicit kill list is what stops engineers burning the
deadline on a login page nobody is judging.

Define the MVP as the smallest thing that demonstrates the core value, write
user stories for the MUST HAVEs, and state success metrics that the Tester can
actually measure.

Weigh every feature as (expected judging value / implementation effort). Cut
anything that scores badly, however interesting it is. Record the cut decisions
with record_decision so nobody relitigates them at hour 20.""",
    tools=PRODUCT_TOOLS,
    write_paths=("PRODUCT/product_plan.md", "AGENT/"),
    requires=("PRODUCT/requirements.md",),
    produces=("PRODUCT/product_plan.md",),
    postconditions=(
        FileContains(
            "PRODUCT/product_plan.md",
            ("must have", "should have", "nice to have", "do not build",
             "mvp", "user stor", "success metric"),
            label="Product plan",
        ),
        HasHeadings("PRODUCT/product_plan.md", 6),
        MinWords("PRODUCT/product_plan.md", 300),
    ),
    context_keys=("problem", "constraints", "judging", "requirements"),
)

STRATEGIST = AgentSpec(
    name="strategist",
    title="Product Strategist",
    team="product",
    mission="""Work out why this wins, and write the argument down.

Cover positioning, differentiation, the defensible advantage, business model,
scalability, and impact. Then, explicitly: the hackathon winning strategy --
map each judging criterion to the specific evidence this project will offer for
it, and name the criterion where we are currently weakest.

Being wrong about where we are weak is worse than being weak. If the honest
answer is "our technical depth is thin relative to the field", say that, and
say what would fix it. The Orchestrator reads this to decide where to spend
remaining time.""",
    tools=PRODUCT_TOOLS,
    write_paths=("PRODUCT/strategy.md", "AGENT/"),
    requires=("PRODUCT/requirements.md",),
    produces=("PRODUCT/strategy.md",),
    postconditions=(
        FileContains(
            "PRODUCT/strategy.md",
            ("positioning", "differentiation", "business model",
             "impact", "judging criteri", "weakest"),
            label="Strategy",
        ),
        HasHeadings("PRODUCT/strategy.md", 5),
        MinWords("PRODUCT/strategy.md", 300),
    ),
    context_keys=("problem", "judging", "requirements", "research"),
)

SPECS = [REQUIREMENTS_ANALYST, PRODUCT_MANAGER, STRATEGIST]
