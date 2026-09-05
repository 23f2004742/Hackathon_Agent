"""Two-stage specialist selection.

Stage 1 is deterministic: regexes over the brief, negation-aware, producing a
capability map. It is fast, free, testable and completely literal -- which is
both why it is kept and why it is not allowed to be the only voice. A brief
that says "clinicians need to see the queue at a glance" implies an interface
without containing the word "frontend", and the regex misses it every time.

Stage 2 asks Claude, once, for a structured plan: which specialists this
problem actually needs, which it does not, and why. It receives the problem
statement, the judging criteria, the constraints and the roster -- not the
project, not the repository, not the previous agents' output. One planning call
that removes eight unnecessary specialists pays for itself many times over.

The two are then merged under rules that keep the guardrails in code:

  * the planner may ADD specialists the regexes missed;
  * the planner may REMOVE specialists the regexes guessed at, except the
    mandatory delivery spine for this project type;
  * a capability the brief states outright cannot be planned away;
  * anything the planner names that is not in the roster is dropped, not
    invented.

Every inclusion and every exclusion carries a reason, and both are persisted,
because "why is there no database engineer on this?" is a question someone
always asks at 3am.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import agents as roster

# ---------------------------------------------------------------------------
# Stage 1 -- deterministic capability detection
# ---------------------------------------------------------------------------

SIGNALS: dict[str, tuple[str, ...]] = {
    "ml": (
        r"\bml\b", r"machine learning", r"\bmodel\b", r"predict", r"forecast",
        r"classif", r"regress", r"train(ing|ed)?\b", r"dataset", r"accuracy",
        r"\bf1\b", r"leaderboard", r"anomaly", r"recommend", r"clustering",
    ),
    "ai": (
        r"\bllm\b", r"\bgpt\b", r"\bclaude\b", r"\bagent(ic|s)?\b", r"\brag\b",
        r"embedding", r"prompt", r"chatbot", r"assistant", r"generative",
        r"natural language", r"\bnlp\b", r"summari[sz]", r"retrieval.augmented",
    ),
    "vision": (
        r"computer vision", r"\bcv\b", r"image (?:classif|recogn|segment)",
        r"object detection", r"\bocr\b", r"camera feed", r"video analys",
    ),
    "frontend": (
        r"\bui\b", r"\bux\b", r"dashboard", r"web app", r"website", r"frontend",
        r"interface", r"visuali[sz]", r"portal", r"\bapp\b", r"\breact\b",
        r"at a glance", r"screen", r"click",
    ),
    "mobile": (
        r"\bmobile\b", r"\bios\b", r"\bandroid\b", r"react native", r"flutter",
        r"\bphone\b", r"\btablet\b",
    ),
    "backend": (
        r"\bapi\b", r"backend", r"server", r"endpoint", r"microservice",
        r"integration", r"webhook", r"rest\b", r"authenticat", r"\bqueue\b",
    ),
    "database": (
        r"database", r"\bsql\b", r"postgres", r"sqlite", r"schema", r"persist",
        r"\bcrud\b", r"store (?:the )?data", r"records?\b", r"inventory",
    ),
    "devops": (
        r"deploy", r"docker", r"\bci/cd\b", r"kubernetes", r"host(ing|ed)?\b",
        r"cloud", r"production", r"\bdemo url\b", r"live (?:site|link)",
    ),
    "design": (
        r"design", r"\bui\b", r"\bux\b", r"user experience", r"interface",
        r"usab", r"accessib", r"visual",
    ),
    "hardware": (
        r"\biot\b", r"hardware", r"sensor", r"raspberry pi", r"arduino",
        r"microcontroller", r"embedded", r"actuator", r"\bdevice\b", r"firmware",
    ),
    "security": (
        r"security", r"\bauth\b", r"encrypt", r"\bpii\b", r"\bgdpr\b", r"hipaa",
        r"compliance", r"vulnerab", r"threat model", r"access control",
    ),
    "payments": (
        r"payment", r"\bstripe\b", r"checkout", r"billing", r"transaction",
        r"\bwallet\b", r"\binvoice\b", r"\bpci\b",
    ),
    "blockchain": (
        r"blockchain", r"smart contract", r"\bweb3\b", r"ethereum", r"solidity",
        r"\bnft\b", r"on-chain", r"\bledger\b(?!\s+file)",
    ),
    "data": (
        # Plurals matter here: a brief says "charts" and "CSVs", never "chart"
        # and "CSV", and a \b-anchored singular silently matches neither.
        r"data analys", r"analytics", r"\bcsvs?\b", r"notebooks?\b", r"jupyter",
        r"\bpandas\b", r"exploratory", r"\beda\b", r"statistic", r"\bcharts?\b",
        r"analys[ei]s?\b.{0,40}\bdata\b", r"\bdata\b.{0,40}\banalys",
        r"usage patterns?\b", r"\bvisuali[sz]ations?\b",
    ),
    "market": (
        r"market", r"business model", r"revenue", r"customer", r"pricing",
        r"competit", r"commercial", r"startup", r"go.to.market", r"\bimpact\b",
    ),
    "research": (
        r"research", r"literature", r"state of the art", r"benchmark",
        r"prior work", r"survey",
    ),
    "presentation": (
        r"present", r"\bpitch\b", r"\bdeck\b", r"slides", r"\bdemo\b",
        r"\bvideo\b", r"judging",
    ),
    "branding": (r"brand", r"name the", r"logo", r"tagline", r"identity"),
}

# A signal implies these specialists.
IMPLIES: dict[str, tuple[str, ...]] = {
    "ml": ("ml_engineer",),
    "ai": ("ai_engineer",),
    "vision": ("ml_engineer",),
    "frontend": ("frontend_engineer", "ux_designer", "ui_designer"),
    "mobile": ("frontend_engineer", "ux_designer"),
    "backend": ("backend_engineer",),
    "database": ("database_engineer",),
    "devops": ("devops_engineer",),
    "design": ("ux_designer", "ui_designer"),
    "security": ("security_reviewer",),
    "payments": ("backend_engineer", "security_reviewer"),
    "blockchain": ("backend_engineer", "security_reviewer"),
    "market": ("market_researcher", "competitor_researcher"),
    "branding": ("brand_designer",),
}

#: Capabilities the roster has no specialist for. Naming them explicitly is
#: how `plan` can say "no hardware engineer exists; this is a gap" instead of
#: silently pretending the project has no hardware in it.
UNSTAFFED = {
    "hardware": ("no hardware/firmware specialist exists in this roster; "
                 "the engineering team will have to cover it"),
    "data": ("no dedicated data-analysis specialist exists in this roster; the "
             "developer covers exploratory analysis, and the ML engineer is "
             "only staffed when there is a model to train"),
}

CORE = (
    "requirements_analyst", "product_manager", "strategist", "architect",
    "tester", "technical_writer", "pitch_strategist", "presentation_builder",
    "demo_engineer", "requirements_auditor", "final_auditor", "submission_manager",
)

#: Never removed by the Claude planner, whatever it thinks. These are the
#: safety and delivery spine: without them nobody checks the work and nobody
#: packages it. Per project type, because a pure research write-up genuinely
#: does not need a demo engineer.
MANDATORY_BY_TYPE: dict[str, tuple[str, ...]] = {
    "default": ("requirements_analyst", "architect", "tester",
                "requirements_auditor", "final_auditor", "submission_manager"),
    "research": ("requirements_analyst", "requirements_auditor",
                 "final_auditor", "submission_manager"),
    "data": ("requirements_analyst", "architect", "tester",
             "requirements_auditor", "final_auditor", "submission_manager"),
}

RESEARCH_DEFAULT = ("technical_researcher", "user_researcher")
RESEARCH_BUSINESS = ("market_researcher", "competitor_researcher")

NEGATORS = re.compile(
    r"\b(no|not|without|never|avoid|avoiding|exclude|excluding|neither|nor|"
    r"don't|doesn't|isn't|aren't|skip|omit|zero)\b"
)


def _negated(text: str, start: int) -> bool:
    """Is this match inside a negative clause?

    Briefs say "no backend, no database" and "no interface required" at least as
    often as they ask for those things. Matching keywords without reading the
    negation staffs a team for work the brief explicitly ruled out, so we look
    back a few words for a negator and stop at clause boundaries.
    """
    window = text[max(0, start - 60):start]
    clause = re.split(r"[.;:!?]|\band\b(?!\s+no\b)", window)[-1]
    return bool(NEGATORS.search(clause))


def detect(text: str) -> dict[str, bool]:
    """True for each capability the brief positively asks for."""
    low = text.lower()
    out: dict[str, bool] = {}
    for key, pats in SIGNALS.items():
        hit = False
        for p in pats:
            for m in re.finditer(p, low):
                if not _negated(low, m.start()):
                    hit = True
                    break
            if hit:
                break
        out[key] = hit
    return out


def negated_capabilities(text: str) -> set[str]:
    """Capabilities the brief explicitly rules out.

    Distinct from "not detected": "no backend" is a statement, and the Claude
    planner is not allowed to overrule it.
    """
    low = text.lower()
    out: set[str] = set()
    for key, pats in SIGNALS.items():
        for p in pats:
            for m in re.finditer(p, low):
                if _negated(low, m.start()):
                    out.add(key)
                    break
    return {k for k in out if not detect(text).get(k)}


PROJECT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai", ("ai",)),
    ("ml", ("ml", "vision")),
    ("web", ("frontend",)),
    ("mobile", ("mobile",)),
    ("backend", ("backend",)),
    ("data", ("data",)),
    ("hardware", ("hardware",)),
    ("blockchain", ("blockchain",)),
    ("payments", ("payments",)),
    ("research", ("research",)),
)


@dataclass
class CapabilityAnalysis:
    """Stage 1's output: what the brief says, before anyone interprets it."""

    capabilities: dict[str, bool] = field(default_factory=dict)
    excluded: set[str] = field(default_factory=set)
    project_type: list[str] = field(default_factory=list)
    complexity: int = 3
    gaps: dict[str, str] = field(default_factory=dict)

    @property
    def present(self) -> list[str]:
        return sorted(k for k, v in self.capabilities.items() if v)

    def to_dict(self) -> dict:
        return {
            "capabilities": self.capabilities,
            "excluded": sorted(self.excluded),
            "project_type": self.project_type,
            "complexity": self.complexity,
            "gaps": self.gaps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CapabilityAnalysis":
        d = d or {}
        return cls(
            capabilities=dict(d.get("capabilities", {})),
            excluded=set(d.get("excluded", [])),
            project_type=list(d.get("project_type", [])),
            complexity=int(d.get("complexity", 3)),
            gaps=dict(d.get("gaps", {})),
        )


def analyse(brief: str) -> CapabilityAnalysis:
    """Stage 1. Deterministic, negation-aware, free."""
    caps = detect(brief)
    excluded = negated_capabilities(brief)
    types = [name for name, keys in PROJECT_TYPES if any(caps.get(k) for k in keys)]
    if not types:
        types = ["software"]

    # Complexity: how many distinct build-shaped capabilities are in play, plus
    # how much brief there is to satisfy.
    build_caps = {"ml", "ai", "vision", "frontend", "mobile", "backend",
                  "database", "devops", "hardware", "blockchain", "payments"}
    breadth = sum(1 for k in build_caps if caps.get(k))
    words = len(brief.split())
    complexity = max(1, min(5, 1 + breadth // 2 + (1 if words > 250 else 0)))

    gaps = {k: why for k, why in UNSTAFFED.items() if caps.get(k)}
    return CapabilityAnalysis(
        capabilities=caps, excluded=excluded, project_type=types,
        complexity=complexity, gaps=gaps,
    )


# ---------------------------------------------------------------------------
# The merged selection
# ---------------------------------------------------------------------------


@dataclass
class Choice:
    """Why one specialist is on the team, and how much of it we expect."""

    agent: str
    reason: str = ""
    priority: str = "medium"        # critical | high | medium | low
    effort: str = "medium"
    source: str = "rules"           # rules | planner | mandatory | coherence

    def to_dict(self) -> dict:
        return {"agent": self.agent, "reason": self.reason,
                "priority": self.priority, "effort": self.effort,
                "source": self.source}


@dataclass
class Selection:
    """The team, the bench, and the reasoning for both.

    `chosen`/`reasons`/`skipped` keep the shape the rest of the system already
    reads; `choices` and `analysis` carry the richer detail the new `plan`
    command shows.
    """

    chosen: set[str] = field(default_factory=set)
    reasons: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    choices: dict[str, Choice] = field(default_factory=dict)
    analysis: CapabilityAnalysis = field(default_factory=CapabilityAnalysis)
    planner_used: bool = False
    planner_note: str = ""

    def pick(self, name: str, why: str, *, priority: str = "medium",
             effort: str = "medium", source: str = "rules") -> None:
        self.chosen.add(name)
        self.reasons.setdefault(name, why)
        self.choices.setdefault(
            name, Choice(name, why, priority, effort, source)
        )

    def skip(self, name: str, why: str) -> None:
        if name not in self.chosen:
            self.skipped[name] = why

    def drop(self, name: str, why: str) -> None:
        """Remove an already-picked specialist and record why."""
        self.chosen.discard(name)
        self.reasons.pop(name, None)
        self.choices.pop(name, None)
        self.skipped[name] = why

    def to_dict(self) -> dict:
        return {
            "chosen": sorted(self.chosen),
            "reasons": self.reasons,
            "skipped": {k: v for k, v in self.skipped.items() if k not in self.chosen},
            "choices": {k: c.to_dict() for k, c in self.choices.items()},
            "analysis": self.analysis.to_dict(),
            "planner_used": self.planner_used,
            "planner_note": self.planner_note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Selection":
        d = d or {}
        sel = cls(
            chosen=set(d.get("chosen", [])),
            reasons=dict(d.get("reasons", {})),
            skipped=dict(d.get("skipped", {})),
            analysis=CapabilityAnalysis.from_dict(d.get("analysis", {})),
            planner_used=bool(d.get("planner_used", False)),
            planner_note=str(d.get("planner_note", "")),
        )
        for name, row in (d.get("choices") or {}).items():
            sel.choices[name] = Choice(**{
                k: v for k, v in row.items()
                if k in Choice.__dataclass_fields__
            })
        return sel


def mandatory_for(types: list[str]) -> tuple[str, ...]:
    """The specialists no planner may remove, for this shape of project."""
    for t in types:
        if t in MANDATORY_BY_TYPE:
            return MANDATORY_BY_TYPE[t]
    return MANDATORY_BY_TYPE["default"]


# ---------------------------------------------------------------------------
# Stage 1 selection (the guardrail)
# ---------------------------------------------------------------------------


def select_by_rules(brief: str, *, depth: str = "full",
                    analysis: CapabilityAnalysis | None = None) -> Selection:
    """Decide which specialists this problem needs, deterministically."""
    a = analysis or analyse(brief)
    sel = Selection(analysis=a)
    signals = a.capabilities
    low = brief.lower()

    for name in CORE:
        sel.pick(name, "core to any hackathon submission",
                 priority="critical" if name in mandatory_for(a.project_type) else "high")

    for name in RESEARCH_DEFAULT:
        sel.pick(name, "establishes the technical path and the user", priority="high")

    if signals.get("market"):
        for name in RESEARCH_BUSINESS:
            sel.pick(name, "brief is judged partly on market/business reasoning")
    else:
        for name in RESEARCH_BUSINESS:
            sel.skip(name, "no market or business-model signal in the brief")

    for signal, present in signals.items():
        for name in IMPLIES.get(signal, ()):
            if present:
                sel.pick(name, f"brief shows '{signal}' signal",
                         priority="high" if signal in ("ml", "ai", "backend") else "medium")
            else:
                sel.skip(name, f"no '{signal}' signal in the brief")

    # Anything that produces code gets the code-quality gates and a generalist
    # to wire it together. `data` and `hardware` are in here because a CLI
    # analysis tool and a firmware sketch are still code somebody has to write,
    # even though the roster has no dedicated specialist for either.
    if any(signals.get(k) for k in
           ("backend", "ml", "ai", "frontend", "vision", "data", "hardware")):
        sel.pick("security_reviewer", "there will be code and possibly credentials",
                 priority="high")
        sel.pick("code_reviewer", "there will be non-trivial code")
        sel.pick("developer", "generalist for wiring and fix tasks")
    else:
        sel.skip("security_reviewer", "no code expected")
        sel.skip("code_reviewer", "no code expected")

    if signals.get("branding") or signals.get("presentation"):
        sel.pick("brand_designer", "naming and pitch identity are in scope", priority="low")
    else:
        sel.skip("brand_designer", "branding not called for; easy place to lose hours")

    # -- coherence pass --------------------------------------------------
    # Signal detection is keyword-based and therefore lopsided: a brief can
    # easily mention usability without ever saying "frontend". These rules
    # repair combinations that cannot actually ship.
    if {"ux_designer", "ui_designer"} & sel.chosen and "frontend_engineer" not in sel.chosen:
        sel.pick("frontend_engineer",
                 "coherence: a UX/UI spec was commissioned, so someone must build it",
                 source="coherence")
    # A model with no surface is hard to demo -- but "no API, no interface" is
    # a statement the brief made, and coherence repair must not quietly
    # overrule it. A notebook submission scored by RMSE genuinely needs no
    # backend, and staffing one costs the team a specialist it cannot spare.
    ruled_out_surface = {"backend", "frontend"} & a.excluded
    if (
        {"ml_engineer", "ai_engineer", "database_engineer"} & sel.chosen
        and not ({"backend_engineer", "frontend_engineer"} & sel.chosen)
        and not ruled_out_surface
    ):
        sel.pick("backend_engineer",
                 "coherence: a model with no surface cannot be demonstrated",
                 source="coherence")
    elif ruled_out_surface and "backend_engineer" not in sel.chosen:
        sel.skip("backend_engineer",
                 "the brief rules out an API/interface; the deliverable is the "
                 "artifact itself, not a service around it")
    if re.search(r"reproducib|from the repo|setup|install|run it|github", low):
        sel.pick("devops_engineer", "brief is judged on a judge being able to run it")

    if depth == "lean":
        for name in ("code_reviewer", "brand_designer", "market_researcher",
                     "competitor_researcher", "database_engineer"):
            if name in sel.chosen:
                sel.drop(name, "dropped: lean mode")

    for name in roster.REGISTRY:
        sel.skip(name, _why_not(name, signals))
    return sel


def _why_not(name: str, signals: dict[str, bool]) -> str:
    """A specific reason a specialist was left off, not a shrug.

    "Not required" tells nobody anything. Naming the capability that would
    have selected it is what lets a human disagree with the decision, which is
    the whole point of recording it.
    """
    wants = sorted(cap for cap, names in IMPLIES.items() if name in names)
    if wants:
        return (f"no {' or '.join(wants)} signal in the brief; "
                f"say so explicitly if the project needs it")
    generic = {
        "database_engineer": "no persistence signal; local files are sufficient here",
        "devops_engineer": "no deployment or reproducibility requirement in the brief",
        "code_reviewer": "no substantial code expected from this brief",
        "developer": "no integration work expected; the specialists cover their own scope",
        "security_reviewer": "no code or credential handling expected",
        "market_researcher": "the brief is not judged on market reasoning",
        "competitor_researcher": "the brief is not judged on competitive positioning",
        "brand_designer": "naming and identity are not in scope; a reliable time sink",
    }
    return generic.get(name, "not implied by any capability the brief states")


# ---------------------------------------------------------------------------
# Stage 2 -- the Claude capability planner
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = """You staff hackathon teams. Given a problem brief, decide which \
specialists a small autonomous team actually needs.

Judge the brief on its substance, not its vocabulary. A brief that says \
"clinicians need to see the queue at a glance" needs an interface even though it \
never says "frontend". A brief that says "tabular leaderboard task scored by RMSE" \
does not need a frontend engineer however many times it says "dashboard" in the \
prize description.

Rules:
- Prefer the SMALLEST team that can deliver and be judged well. Every extra \
specialist costs time the team does not have.
- Only name agents from the roster you are given. Do not invent roles.
- Give a specific reason for every inclusion AND every exclusion. "Not needed" \
is not a reason; "the submission is a CLI with no web surface" is.
- Return ONLY a JSON object. No prose, no markdown fence."""

PLANNER_SCHEMA = """{
  "project_type": ["ai", "web"],
  "complexity": 1-5,
  "required_specialists": [
    {"agent": "<roster name>", "reason": "<why this project needs it>",
     "priority": "critical|high|medium|low", "estimated_effort": "low|medium|high"}
  ],
  "optional_specialists": [
    {"agent": "<roster name>", "reason": "<what it would add if time allows>"}
  ],
  "excluded_specialists": [
    {"agent": "<roster name>", "reason": "<why this project does not need it>"}
  ],
  "notes": "<one sentence on the biggest risk in this staffing>"
}"""


def planner_prompt(brief: str) -> str:
    """The whole prompt. Deliberately only the brief -- not the project."""
    lines = [
        "## Roster (use these exact names)", "",
    ]
    for team, specs in roster.TEAMS.items():
        names = ", ".join(s.name for s in specs)
        lines.append(f"- **{team}**: {names}")
    lines += [
        "", "## Brief", "", brief.strip()[:6000], "",
        "## Required output shape", "", PLANNER_SCHEMA,
    ]
    return "\n".join(lines)


def parse_plan(raw: str) -> dict | None:
    """Pull a JSON object out of a model reply, tolerantly."""
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def apply_plan(sel: Selection, plan: dict, *, brief: str = "") -> Selection:
    """Merge a Claude plan into a rules-based selection, guardrails intact."""
    a = sel.analysis
    mandatory = set(mandatory_for(a.project_type))
    stated = {c for c, v in a.capabilities.items() if v}

    for row in plan.get("required_specialists") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("agent", "")).strip()
        if name not in roster.REGISTRY:
            continue
        why = str(row.get("reason", "")).strip() or "selected by the capability planner"
        if name in sel.chosen:
            # Keep the model's reasoning; it is better prose than the regex's.
            sel.reasons[name] = why
            if name in sel.choices:
                sel.choices[name].reason = why
                sel.choices[name].priority = str(row.get("priority", "medium")).lower()
                sel.choices[name].effort = str(row.get("estimated_effort", "medium")).lower()
        else:
            sel.pick(name, why,
                     priority=str(row.get("priority", "medium")).lower(),
                     effort=str(row.get("estimated_effort", "medium")).lower(),
                     source="planner")

    for row in plan.get("excluded_specialists") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("agent", "")).strip()
        if name not in roster.REGISTRY:
            continue
        why = str(row.get("reason", "")).strip() or "excluded by the capability planner"
        if name in mandatory:
            sel.reasons.setdefault(name, "")
            sel.reasons[name] = (
                f"kept despite the planner ({why}): mandatory for a "
                f"{'/'.join(a.project_type)} project"
            )
            continue
        # A capability the brief states outright is not the planner's to remove.
        implied_by = {c for c, names in IMPLIES.items() if name in names}
        if implied_by & stated:
            sel.reasons[name] = (
                f"kept despite the planner: the brief states "
                f"{'/'.join(sorted(implied_by & stated))} outright"
            )
            continue
        sel.drop(name, f"planner: {why}")

    for row in plan.get("optional_specialists") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("agent", "")).strip()
        if name in roster.REGISTRY and name not in sel.chosen:
            sel.skip(name, "planner: optional, dropped to keep the team small — "
                           + str(row.get("reason", "")).strip())

    types = [str(t) for t in (plan.get("project_type") or []) if isinstance(t, str)]
    if types:
        sel.analysis.project_type = types
    if isinstance(plan.get("complexity"), int):
        sel.analysis.complexity = max(1, min(5, plan["complexity"]))
    sel.planner_used = True
    sel.planner_note = str(plan.get("notes", "")).strip()

    # A last coherence sweep: the planner can produce a team that cannot ship.
    _repair(sel)
    return sel


def _repair(sel: Selection) -> None:
    """Fix staffing that cannot physically deliver, whoever proposed it."""
    if {"ux_designer", "ui_designer"} & sel.chosen and "frontend_engineer" not in sel.chosen:
        sel.pick("frontend_engineer",
                 "coherence: a UX/UI spec was commissioned, so someone must build it",
                 source="coherence")
    if "presentation_builder" in sel.chosen and "pitch_strategist" not in sel.chosen:
        sel.pick("pitch_strategist",
                 "coherence: a deck with no argument behind it wastes the slot",
                 source="coherence")
    for name in mandatory_for(sel.analysis.project_type):
        if name not in sel.chosen:
            sel.pick(name, "mandatory: safety and delivery spine", source="mandatory",
                     priority="critical")


def plan_with_claude(brief: str, backend, *, verbose: bool = False) -> dict | None:
    """Ask the backend for a specialist plan. Returns None if it cannot.

    Failure here is not an error: the deterministic selection is a complete,
    working answer on its own, and a planning call that fails must not take a
    hackathon down with it.
    """
    ask = getattr(backend, "ask_json", None)
    if ask is None:
        return None
    try:
        raw = ask(system=PLANNER_SYSTEM, user=planner_prompt(brief), purpose="specialist plan")
    except NotImplementedError:
        return None
    except Exception:  # noqa: BLE001 - planning is best-effort by design
        return None
    return parse_plan(raw or "")


def select_specialists(
    brief: str,
    *,
    depth: str = "full",
    backend=None,
    intelligent: bool = True,
    verbose: bool = False,
) -> Selection:
    """The full two-stage selection. Backend optional; rules always run."""
    a = analyse(brief)
    sel = select_by_rules(brief, depth=depth, analysis=a)
    if not intelligent or backend is None:
        return sel
    plan = plan_with_claude(brief, backend, verbose=verbose)
    if plan is None:
        sel.planner_note = "capability planner unavailable; deterministic selection stands"
        return sel
    return apply_plan(sel, plan, brief=brief)
