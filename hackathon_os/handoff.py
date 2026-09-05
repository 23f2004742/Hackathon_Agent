"""The handoff protocol: the structured result every specialist returns.

This is the only channel through which a specialist reports back. The
Orchestrator never reads an agent's prose -- it reads this. That is what makes
coordination reliable rather than vibes-based.

A specialist cannot report `completed` without having produced the artifacts its
spec declares; `AgentResult.validate_against` enforces that in code, not in a
prompt. See agents/base.py for where it is applied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class Status(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    NEEDS_HUMAN = "needs_human"


class Priority(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def rank(self) -> int:
        return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[self.value]


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class Artifact:
    """A file a specialist produced. `path` is relative to the project root."""

    path: str
    kind: str = "markdown"
    description: str = ""
    bytes: int = 0

    def exists(self, root: Path) -> bool:
        return (root / self.path).is_file()


@dataclass
class Finding:
    """Something discovered that another specialist may need to act on."""

    summary: str
    severity: Severity = Severity.INFO
    evidence: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity)


@dataclass
class Decision:
    """A choice made, recorded so it can be revisited by self-correction."""

    what: str
    why: str
    alternatives: list[str] = field(default_factory=list)
    reversible: bool = True


@dataclass
class NextTask:
    """A task this specialist believes should happen next.

    The Orchestrator treats these as *proposals*. It decides whether to
    schedule them; an agent cannot inject work directly into the graph.
    """

    agent: str
    objective: str
    priority: Priority = Priority.MEDIUM
    reason: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.priority, str):
            self.priority = Priority(self.priority)


@dataclass
class AgentResult:
    """The single structured value a specialist hands back."""

    status: Status = Status.COMPLETED
    summary: str = ""
    artifacts: list[Artifact] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_tasks: list[NextTask] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)

    # Bookkeeping the Orchestrator uses for cost control and the dashboard.
    agent: str = ""
    task_id: str = ""
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = Status(self.status)

    # -- validation ------------------------------------------------------

    def validate_against(self, spec, root: Path) -> "AgentResult":
        """Downgrade an over-optimistic result to FAILED.

        An agent that says `completed` but left its declared artifacts on the
        floor did not complete. We check the filesystem rather than trusting
        the claim, and we record the artifacts we actually found so the
        Orchestrator's view matches disk.
        """
        if self.status is not Status.COMPLETED:
            return self

        missing: list[str] = []
        found: list[Artifact] = []
        for rel in spec.produces:
            fp = root / rel
            if fp.is_file() and fp.stat().st_size > spec.min_artifact_bytes:
                found.append(
                    Artifact(
                        path=rel,
                        kind=_kind_of(rel),
                        description=f"produced by {spec.name}",
                        bytes=fp.stat().st_size,
                    )
                )
            else:
                why = "missing" if not fp.is_file() else f"under {spec.min_artifact_bytes}B"
                missing.append(f"{rel} ({why})")

        # Keep any extra artifacts the agent reported that do exist on disk.
        declared = {a.path for a in found}
        for a in self.artifacts:
            if a.path not in declared and a.exists(root):
                a.bytes = (root / a.path).stat().st_size
                found.append(a)
        self.artifacts = found

        if missing:
            self.status = Status.FAILED
            self.notes.append(
                "artifact contract not met: " + "; ".join(missing)
            )
            return self

        problem = spec.check_postconditions(root)
        if problem:
            self.status = Status.FAILED
            self.notes.append(f"postcondition failed: {problem}")
        return self

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        for f in d["findings"]:
            f["severity"] = (
                f["severity"].value if isinstance(f["severity"], Severity) else f["severity"]
            )
        for t in d["next_tasks"]:
            t["priority"] = (
                t["priority"].value if isinstance(t["priority"], Priority) else t["priority"]
            )
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentResult":
        d = dict(d)
        d["artifacts"] = [Artifact(**a) for a in d.get("artifacts", [])]
        d["findings"] = [Finding(**f) for f in d.get("findings", [])]
        d["decisions"] = [Decision(**x) for x in d.get("decisions", [])]
        d["next_tasks"] = [NextTask(**t) for t in d.get("next_tasks", [])]
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def blocked(cls, agent: str, missing: list[str]) -> "AgentResult":
        return cls(
            status=Status.BLOCKED,
            agent=agent,
            summary=f"{agent} cannot start: required inputs absent.",
            blocked_by=missing,
        )

    @property
    def critical_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.CRITICAL]


def _kind_of(rel: str) -> str:
    ext = Path(rel).suffix.lower().lstrip(".")
    return {
        "md": "markdown", "json": "json", "py": "code", "html": "html",
        "csv": "data", "pptx": "presentation", "pdf": "pdf", "zip": "archive",
        "txt": "text", "yml": "config", "yaml": "config", "toml": "config",
    }.get(ext, ext or "file")
