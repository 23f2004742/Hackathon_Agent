"""Design specialists. They write specifications the Frontend Engineer builds
against; they do not write application code themselves."""

from __future__ import annotations

from ..base import AgentSpec, FileContains, HasHeadings, MinWords

DESIGN_TOOLS = (
    "read_file", "write_file", "search_code",
    "knowledge_search", "record_decision", "read_decisions",
)

UX_DESIGNER = AgentSpec(
    name="ux_designer",
    title="UX Designer",
    team="design",
    mission="""Define how the product works before anyone builds it.

Cover the user journey end to end, information architecture, the interaction
flow for the core task, and the friction points you are removing. Specify
onboarding, the empty state, and the error state -- these three are what
separate a product from a prototype, and they are what a judge notices.

Count the clicks from landing to core value. If it is more than three, redesign
it. The demo has two minutes.

Write a specification precise enough that the Frontend Engineer does not have
to invent anything, and short enough that they will actually read it.""",
    tools=DESIGN_TOOLS,
    write_paths=("DESIGN/ux.md", "AGENT/"),
    requires=("PRODUCT/product_plan.md",),
    produces=("DESIGN/ux.md",),
    postconditions=(
        FileContains(
            "DESIGN/ux.md",
            ("user journey", "information architecture", "onboarding",
             "empty state", "error state"),
            label="UX spec",
        ),
        HasHeadings("DESIGN/ux.md", 5),
        MinWords("DESIGN/ux.md", 250),
    ),
    context_keys=("problem", "product_plan", "research"),
    effort="medium",
)

UI_DESIGNER = AgentSpec(
    name="ui_designer",
    title="UI Designer",
    team="design",
    mission="""Define the visual system: hierarchy, type scale, spacing, colour, components
and responsive behaviour.

Give concrete values -- hex codes, a type scale in px or rem, a spacing unit --
not adjectives. "Clean and modern" is not a specification; `--space: 8px` is.

Check contrast: a projector washes out everything subtle, so body text needs
more contrast than your monitor suggests. State both a light and a dark
treatment if the demo environment is unknown.

Prefer an existing design system over inventing one. Hackathon time spent on a
bespoke component library is time not spent on what is judged.""",
    tools=DESIGN_TOOLS,
    write_paths=("DESIGN/ui.md", "AGENT/"),
    requires=("DESIGN/ux.md",),
    produces=("DESIGN/ui.md",),
    postconditions=(
        FileContains(
            "DESIGN/ui.md",
            ("typography", "spacing", "color", "component", "responsive", "contrast"),
            label="UI spec",
        ),
        HasHeadings("DESIGN/ui.md", 5),
        MinWords("DESIGN/ui.md", 250),
    ),
    context_keys=("product_plan", "design"),
    effort="medium",
)

BRAND_DESIGNER = AgentSpec(
    name="brand_designer",
    title="Brand Designer",
    team="design",
    mission="""Name the thing and give it a voice. Timebox this hard.

Produce: product name, tagline, a one-line positioning statement, and the
visual language for the pitch. A memorable name and a tagline a judge can
repeat are worth real points; a logo exploration is not.

The tagline states what it does for whom, not how it feels. Deliver, then stop
-- branding is the easiest place in a hackathon to lose three hours.""",
    tools=DESIGN_TOOLS,
    write_paths=("DESIGN/brand.md", "AGENT/"),
    requires=("PRODUCT/strategy.md",),
    produces=("DESIGN/brand.md",),
    postconditions=(
        FileContains(
            "DESIGN/brand.md",
            ("product name", "tagline", "positioning"),
            label="Brand guide",
        ),
        MinWords("DESIGN/brand.md", 120),
    ),
    context_keys=("problem", "strategy"),
    model="claude-sonnet-5",
    effort="medium",
    typical_cost="low",
)

SPECS = [UX_DESIGNER, UI_DESIGNER, BRAND_DESIGNER]
