"""Dynamic model selection.

`routing.py` decides *what kind of work* a specialist does and therefore how
much effort to spend on it. This module decides *which model* runs it, and it
starts from a different premise: the default model is presumed sufficient, and
anything stronger has to be argued for on this specific task.

That is the opposite of the previous behaviour, where a role's tier chose the
model once and forever. Seven specialists were permanently pinned to Opus, so
a trivial architecture task in a three-file CLI project burned the plan's
separate weekly Opus window exactly as hard as a genuinely hard one.

The policy here:

    default -> assess this task -> upgrade only with a stated reason

Every decision carries a reason and a confidence and is written to project
state, so `hackathon plan` can show it and `hackathon resume` can honour it.
Once a task starts on a model it stays there; escalation happens only after a
failure or an explicit capability limit, and the escalation is recorded.

Model identifiers are configurable, because they do go stale. `MODELS` is the
default table; `HACKATHON_MODELS` (a JSON file) overrides it, and
`HACKATHON_DEFAULT_MODEL` changes which alias `default` resolves to.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .routing import TIERS, Tier, tier_name

# Env hooks. Model ids age; nothing here hard-codes one as eternal truth.
DEFAULT_MODEL_ENV = "HACKATHON_DEFAULT_MODEL"
MODELS_FILE_ENV = "HACKATHON_MODELS"

#: alias -> concrete model id. `default` is an alias for one of the others.
MODELS: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
}

#: Cheapest first. Escalation walks this list.
LADDER: tuple[str, ...] = ("haiku", "sonnet", "opus")

#: The alias `default` resolves to unless configured otherwise. Sonnet is the
#: right default: capable enough for almost every specialist, and it does not
#: touch the plan's separate Opus window.
BASE_DEFAULT = "sonnet"


class UnknownModel(ValueError):
    """An alias or model id we will not run. Better than silently guessing."""


def _overrides() -> dict[str, str]:
    path = os.environ.get(MODELS_FILE_ENV, "").strip()
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}


def catalogue() -> dict[str, str]:
    """The alias table in force, including `default`."""
    table = dict(MODELS)
    table.update(_overrides())
    table["default"] = table.get(default_alias(), table.get(BASE_DEFAULT, "claude-sonnet-5"))
    return table


def default_alias() -> str:
    """Which real alias `default` currently means."""
    want = os.environ.get(DEFAULT_MODEL_ENV, "").strip().lower() or BASE_DEFAULT
    return want if want in MODELS or want in _overrides() else BASE_DEFAULT


def resolve(alias: str) -> str:
    """Alias (or a literal model id) -> concrete model id, or raise.

    Rejecting an unknown name here is deliberate: a typo in `--model` that
    silently fell through to the default would spend a whole run on the wrong
    model and look like it worked.
    """
    if not alias:
        raise UnknownModel("no model given")
    key = alias.strip().lower()
    table = catalogue()
    if key in table:
        return table[key]
    if key in set(table.values()):
        return key
    raise UnknownModel(
        f"unknown model '{alias}'. Known aliases: {', '.join(sorted(table))}; "
        f"known ids: {', '.join(sorted(set(table.values())))}"
    )


def alias_for(model_id: str) -> str:
    """The friendly alias for a concrete id, or the id itself."""
    for name in LADDER:
        if catalogue().get(name) == model_id:
            return name
    return model_id


def is_valid(alias: str) -> bool:
    try:
        resolve(alias)
    except UnknownModel:
        return False
    return True


def stronger(alias: str) -> str:
    """The next model up the ladder, or the same one if already at the top."""
    key = alias if alias in LADDER else alias_for(resolve(alias))
    if key not in LADDER:
        return key
    return LADDER[min(LADDER.index(key) + 1, len(LADDER) - 1)]


def rank(alias: str) -> int:
    key = alias if alias in LADDER else alias_for(resolve(alias))
    return LADDER.index(key) if key in LADDER else len(LADDER)


# ---------------------------------------------------------------------------
# What makes a task hard
# ---------------------------------------------------------------------------

#: How much genuine reasoning each role's output demands, 0-3. This is about
#: the *kind* of thinking, not the role's seniority: an architect making a
#: routine three-service decision does not need more model than a tester
#: reading a stack trace, and the score below is only one input.
REASONING_WEIGHT: dict[str, int] = {
    "architect": 3,
    "final_auditor": 3,
    "security_reviewer": 3,
    "ml_engineer": 3,
    "ai_engineer": 2,
    "developer": 2,
    "strategist": 2,
    "code_reviewer": 2,
    "backend_engineer": 2,
    "requirements_analyst": 1,
    "product_manager": 1,
    "tester": 1,
    "frontend_engineer": 1,
    "technical_researcher": 1,
    "requirements_auditor": 1,
    "database_engineer": 1,
    "devops_engineer": 1,
    "user_researcher": 1,
    "market_researcher": 1,
    "competitor_researcher": 1,
    "pitch_strategist": 1,
    "ux_designer": 0,
    "ui_designer": 0,
    "technical_writer": 0,
    "demo_engineer": 0,
    "brand_designer": 0,
    "presentation_builder": 0,
    "submission_manager": 0,
}

#: Mechanical work. Formatting, packaging, transforms -- there is no judgement
#: to buy, so these drop *below* the default rather than sitting on it.
MECHANICAL = frozenset({"presentation_builder", "submission_manager", "brand_designer"})

#: The score at or above which an upgrade past the default is justified.
UPGRADE_AT = 6


@dataclass
class ModelDecision:
    """One task's model, with the reasoning that chose it."""

    task: str
    agent: str = ""
    model: str = "default"          # the alias, as the operator would type it
    model_id: str = ""              # the concrete id actually sent
    reason: str = ""
    confidence: float = 0.8
    score: int = 0
    forced: bool = False            # set by --model or HACKATHON_TIER
    escalations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task": self.task, "agent": self.agent, "model": self.model,
            "model_id": self.model_id, "reason": self.reason,
            "confidence": round(self.confidence, 2), "score": self.score,
            "forced": self.forced, "escalations": list(self.escalations),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelDecision":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    def render(self) -> str:
        esc = ""
        if self.escalations:
            last = self.escalations[-1]
            esc = f"  (escalated {last.get('from')} -> {last.get('to')}: {last.get('reason','')})"
        return f"{self.task:<16} {self.model:<8} {self.reason}{esc}"


class ModelPlanner:
    """Chooses a model per task, defaulting down and upgrading on evidence."""

    def __init__(
        self,
        *,
        override: str = "",
        project_complexity: int = 3,
        decisions: dict[str, ModelDecision] | None = None,
    ) -> None:
        # Validate eagerly: a bad --model must fail before any work is done.
        if override:
            resolve(override)
        self.override = (override or "").strip().lower()
        self.project_complexity = max(1, min(5, project_complexity))
        self.decisions: dict[str, ModelDecision] = dict(decisions or {})

    # -- the decision -----------------------------------------------------

    def decide(self, task, spec, *, capabilities: dict | None = None) -> ModelDecision:
        """Pick the model for one task. Idempotent: an existing decision stands.

        Keeping the recorded decision is what stops a model flapping between
        waves, and what makes a resumed run replay from the ledger rather than
        re-running everything at a newly chosen model.
        """
        existing = self.decisions.get(task.id)
        if existing is not None and not self.override:
            return existing

        if self.override:
            d = ModelDecision(
                task=task.id, agent=spec.name, model=self.override,
                model_id=resolve(self.override), forced=True, confidence=1.0,
                reason="explicitly requested with --model",
            )
            self.decisions[task.id] = d
            return d

        # A forced tier (HACKATHON_TIER) is an operator instruction too.
        forced_tier = os.environ.get("HACKATHON_TIER", "").strip().lower()
        if forced_tier in TIERS:
            alias = alias_for(TIERS[forced_tier].model)
            d = ModelDecision(
                task=task.id, agent=spec.name, model=alias, model_id=resolve(alias),
                forced=True, confidence=1.0,
                reason=f"HACKATHON_TIER={forced_tier} pins the whole run",
            )
            self.decisions[task.id] = d
            return d

        score, factors = self._score(task, spec, capabilities or {})
        base = default_alias()

        if spec.name in MECHANICAL and score < UPGRADE_AT:
            alias = "haiku"
            reason = ("mechanical transform with no judgement to buy; below the "
                      "default is correct here")
            confidence = 0.9
        elif score >= UPGRADE_AT:
            alias = stronger(base)
            reason = f"complexity {score}/10 justifies an upgrade: {', '.join(factors)}"
            confidence = min(0.95, 0.6 + 0.05 * (score - UPGRADE_AT))
        else:
            alias = base
            reason = f"standard task (complexity {score}/10); default model is sufficient"
            confidence = min(0.95, 0.7 + 0.04 * (UPGRADE_AT - score))

        d = ModelDecision(
            task=task.id, agent=spec.name, model=alias, model_id=resolve(alias),
            reason=reason, confidence=confidence, score=score,
        )
        self.decisions[task.id] = d
        return d

    def _score(self, task, spec, capabilities: dict) -> tuple[int, list[str]]:
        """0-10. Only genuinely hard work should reach UPGRADE_AT."""
        factors: list[str] = []
        score = REASONING_WEIGHT.get(spec.name, 1) * 2
        if score:
            factors.append(f"reasoning weight {REASONING_WEIGHT.get(spec.name, 1)}")

        prio = getattr(getattr(task, "priority", None), "value", "MEDIUM")
        if prio == "CRITICAL":
            score += 1
            factors.append("critical priority")

        if getattr(task, "effort", 3) >= 4:
            score += 1
            factors.append("high effort")

        if self.project_complexity >= 4:
            score += 1
            factors.append("complex project")

        # A retry is evidence the cheaper bet was wrong.
        attempts = getattr(task, "attempts", 0)
        if attempts:
            score += attempts
            factors.append(f"{attempts} previous attempt(s)")

        # Domains where a wrong answer is expensive and hard to detect.
        risky = {"security", "payments", "blockchain", "ml", "hardware"}
        if risky & {k for k, v in capabilities.items() if v}:
            if spec.name in ("architect", "security_reviewer", "final_auditor",
                             "ml_engineer", "backend_engineer"):
                score += 1
                factors.append("high-risk domain in scope")

        return max(0, min(10, score)), factors

    # -- escalation -------------------------------------------------------

    def escalate(self, task_id: str, reason: str) -> ModelDecision | None:
        """Move one task up the ladder, once, and record why.

        Called only after a failure or a stated capability limit. Never called
        mid-task: switching models inside a task throws away everything the
        first model had already worked out.
        """
        d = self.decisions.get(task_id)
        if d is None or d.forced:
            return d
        up = stronger(d.model)
        if up == d.model:
            return d
        d.escalations.append({"from": d.model, "to": up, "reason": reason})
        d.model, d.model_id = up, resolve(up)
        d.reason = f"escalated after failure: {reason}"
        d.confidence = 0.75
        return d

    # -- integration with routing -----------------------------------------

    def tier_for(self, task, spec, *, capabilities: dict | None = None) -> tuple[Tier, ModelDecision]:
        """The effective Tier to run with: routed effort, planned model.

        `routing` still owns effort and turn limits -- it encodes what kind of
        work the role does -- but the model comes from here.
        """
        d = self.decide(task, spec, capabilities=capabilities)
        base = TIERS[tier_name(spec.name)]
        tier = Tier(
            name=base.name, model=d.model_id, effort=base.effort,
            max_turns=base.max_turns, why=base.why,
        )
        return tier, d

    # -- reporting / persistence ------------------------------------------

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.decisions.values():
            out[d.model] = out.get(d.model, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {tid: d.to_dict() for tid, d in self.decisions.items()}

    @classmethod
    def from_dict(cls, d: dict, **kw) -> "ModelPlanner":
        decisions = {
            tid: ModelDecision.from_dict(row) for tid, row in (d or {}).items()
        }
        return cls(decisions=decisions, **kw)
