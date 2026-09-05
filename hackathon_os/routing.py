"""Model routing: spend the scarce budget where judgement actually changes the score.

On a subscription the currency is not dollars, it is rate-limit windows -- and
they are not one pool. Claude enforces a five-hour window, a seven-day window,
and a *separate* seven-day window for Opus. A hackathon run that sends all 28
specialists to Opus exhausts the Opus week on slide assembly and submission
checklists, and then has nothing left for the architecture decision that
actually decides whether the project works.

So each specialist is routed by the kind of work it does, not by seniority:

  deep      the decisions everything downstream inherits, and the audit that
            catches what the team missed. Wrong here is expensive.
  build     code that has to run. Correctness is checkable, so effort buys more
            than raw capability does.
  standard  analysis and prose. Judged by a human reading it once.
  light     mechanical transforms -- markdown to slides, files to a checklist.
            There is no judgement to buy.

A failed task escalates one tier on retry: the cheap attempt is the hypothesis,
and paying more is how we test it, rather than repeating the same call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Set to a tier name to force every specialist onto it (useful for a dry run
# that must not touch the Opus window at all).
FORCE_TIER_ENV = "HACKATHON_TIER"


@dataclass(frozen=True)
class Tier:
    name: str
    model: str
    effort: str
    max_turns: int
    why: str


TIERS: dict[str, Tier] = {
    "deep": Tier(
        "deep", "claude-opus-5", "xhigh", 60,
        "decisions the rest of the run is built on top of",
    ),
    "build": Tier(
        "build", "claude-sonnet-5", "xhigh", 60,
        "code that must actually run; effort beats capability when tests can check it",
    ),
    "standard": Tier(
        "standard", "claude-sonnet-5", "medium", 40,
        "analysis and prose a judge reads once",
    ),
    "light": Tier(
        "light", "claude-haiku-4-5", "low", 25,
        "mechanical transform with no judgement to buy",
    ),
}

ORDER = ("light", "standard", "build", "deep")

# Every specialist, placed deliberately. A name missing from here is a new
# specialist whose author did not think about cost -- `route` says so loudly
# rather than defaulting it onto Opus.
ROLE_TIER: dict[str, str] = {
    # -- deep: gets it wrong and everything downstream is wrong ---------------
    "architect": "deep",
    "strategist": "deep",
    "developer": "deep",
    "ai_engineer": "deep",
    "ml_engineer": "deep",
    "security_reviewer": "deep",
    "final_auditor": "deep",
    # -- build: it has to run ------------------------------------------------
    "backend_engineer": "build",
    "frontend_engineer": "build",
    "database_engineer": "build",
    "devops_engineer": "build",
    "demo_engineer": "build",
    "tester": "build",
    "code_reviewer": "build",
    # -- standard: read once, by a human -------------------------------------
    "market_researcher": "standard",
    "competitor_researcher": "standard",
    "technical_researcher": "standard",
    "user_researcher": "standard",
    "requirements_analyst": "standard",
    "product_manager": "standard",
    "requirements_auditor": "standard",
    "ux_designer": "standard",
    "ui_designer": "standard",
    "technical_writer": "standard",
    "pitch_strategist": "standard",
    # -- light: a transform, not a judgement ---------------------------------
    "brand_designer": "light",
    "presentation_builder": "light",
    "submission_manager": "light",
}

DEFAULT_TIER = "standard"


def tier_name(agent: str) -> str:
    forced = os.environ.get(FORCE_TIER_ENV, "").strip().lower()
    if forced in TIERS:
        return forced
    return ROLE_TIER.get(agent, DEFAULT_TIER)


def escalate(name: str) -> str:
    """The next tier up, or the same one if already at the top."""
    i = ORDER.index(name)
    return ORDER[min(i + 1, len(ORDER) - 1)]


def route(spec, attempt: int = 0) -> Tier:
    """Pick the tier for one run of one specialist.

    The table is authoritative. A spec that pins a model is honoured only when
    the pin is *cheaper* than the routed tier -- an author asking for less may
    know something we do not, but a stale pin must never quietly escalate a run
    onto Opus. Each failed attempt escalates one step.
    """
    name = tier_name(spec.name)
    pinned = _pinned_tier(spec)
    if pinned and ORDER.index(pinned) < ORDER.index(name):
        name = pinned
    for _ in range(max(0, attempt)):
        name = escalate(name)
    return TIERS[name]


def _pinned_tier(spec) -> str:
    """The tier a spec's explicit model belongs to, if it pinned one at all."""
    from .agents.base import AgentSpec

    if spec.model == AgentSpec.__dataclass_fields__["model"].default:
        return ""
    for name in ORDER:
        if TIERS[name].model == spec.model:
            return name
    return ""


def unrouted(roster_names) -> list[str]:
    """Specialists with no deliberate tier. Asserted empty by the test suite."""
    return sorted(n for n in roster_names if n not in ROLE_TIER)


def plan(roster_names) -> dict[str, list[str]]:
    """Tier -> specialists, for `hackathon.py agents --routing`."""
    out: dict[str, list[str]] = {t: [] for t in reversed(ORDER)}
    for n in sorted(roster_names):
        out.setdefault(tier_name(n), []).append(n)
    return out
