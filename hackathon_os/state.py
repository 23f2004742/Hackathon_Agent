"""Project state: the middle memory layer.

Everything the Orchestrator needs to resume a hackathon after a crash, a
context reset, or a night's sleep lives in <project>/AGENT/state.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .handoff import AgentResult, Status
from .planner import Selection
from .taskgraph import TaskGraph
from .token_optimizer import Budget, OptimizerMetrics

STATE_FILE = "AGENT/state.json"

PHASES = ["intake", "research", "plan", "design", "build", "validate", "deliver", "done"]

# The project skeleton every specialist's write scope assumes.
DIRECTORIES = [
    "AGENT", "RESEARCH", "PRODUCT", "DESIGN", "DOCUMENTATION",
    "VALIDATION", "DEMO", "PRESENTATION", "SUBMISSION", "FINAL",
    "src", "tests", "data",
]

BRIEF_FILES = {
    "problem_statement": "AGENT/problem_statement.md",
    "judging_criteria": "AGENT/judging_criteria.md",
    "submission_requirements": "AGENT/submission_requirements.md",
    "constraints": "AGENT/constraints.md",
}


@dataclass
class ProjectState:
    root: Path
    name: str = ""
    phase: str = "intake"
    graph: TaskGraph = field(default_factory=TaskGraph)
    history: list[AgentResult] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    backend: str = "auto"
    notes: list[str] = field(default_factory=list)

    # -- what the planners decided, persisted so a resume never re-decides ---
    # Re-deciding on resume would be worse than useless: the ledger is keyed by
    # the model a task ran on, so a fresh decision silently invalidates every
    # completed task and re-runs the whole hackathon.
    selection: Selection = field(default_factory=Selection)
    model_decisions: dict = field(default_factory=dict)
    budgets: dict = field(default_factory=dict)
    token_metrics: OptimizerMetrics = field(default_factory=OptimizerMetrics)
    replans: list[dict] = field(default_factory=list)

    # -- lifecycle -------------------------------------------------------

    @classmethod
    def create(
        cls,
        root: Path,
        name: str,
        *,
        problem: str,
        judging: str = "",
        submission: str = "",
        constraints: str = "",
    ) -> "ProjectState":
        root.mkdir(parents=True, exist_ok=True)
        for d in DIRECTORIES:
            (root / d).mkdir(parents=True, exist_ok=True)

        written = {
            "problem_statement": problem,
            "judging_criteria": judging,
            "submission_requirements": submission,
            "constraints": constraints,
        }
        titles = {
            "problem_statement": "Problem Statement",
            "judging_criteria": "Judging Criteria",
            "submission_requirements": "Submission Requirements",
            "constraints": "Constraints",
        }
        for key, body in written.items():
            if not body.strip():
                continue
            p = root / BRIEF_FILES[key]
            p.write_text(f"# {titles[key]}\n\n{body.strip()}\n", encoding="utf-8")

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st = cls(root=root, name=name, created=now, updated=now)
        st.save()
        return st

    @classmethod
    def load(cls, root: Path) -> "ProjectState":
        p = root / STATE_FILE
        if not p.is_file():
            raise FileNotFoundError(
                f"no hackathon project at {root} (expected {STATE_FILE}). "
                f"Run: hackathon init <name>"
            )
        d = json.loads(p.read_text(encoding="utf-8"))
        st = cls(
            root=root,
            name=d.get("name", root.name),
            phase=d.get("phase", "intake"),
            graph=TaskGraph.from_dict(d.get("graph", {})),
            history=[AgentResult.from_dict(r) for r in d.get("history", [])],
            created=d.get("created", ""),
            updated=d.get("updated", ""),
            backend=d.get("backend", "auto"),
            notes=d.get("notes", []),
            selection=Selection.from_dict(d.get("selection", {})),
            model_decisions=d.get("model_decisions", {}),
            budgets=d.get("budgets", {}),
            token_metrics=OptimizerMetrics.from_dict(d.get("token_metrics", {})),
            replans=d.get("replans", []),
        )
        return st

    def save(self) -> None:
        self.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        p = self.root / STATE_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "phase": self.phase,
                    "created": self.created,
                    "updated": self.updated,
                    "backend": self.backend,
                    "notes": self.notes,
                    "selection": self.selection.to_dict(),
                    "model_decisions": self.model_decisions,
                    "budgets": self.budgets,
                    "token_metrics": self.token_metrics.to_dict(),
                    "replans": self.replans,
                    "graph": self.graph.to_dict(),
                    "history": [r.to_dict() for r in self.history],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- reads -----------------------------------------------------------

    @property
    def brief(self) -> str:
        p = self.root / BRIEF_FILES["problem_statement"]
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

    @property
    def full_brief(self) -> str:
        """Everything the organisers told us.

        Specialist selection reads this rather than the problem statement
        alone: constraints and judging criteria routinely carry the signal that
        decides whether a role is needed at all (a "Python-only backend"
        constraint, a reproducibility criterion), and reading only the problem
        statement silently under-staffs the team.
        """
        parts = []
        for key in ("problem_statement", "constraints", "judging_criteria",
                    "submission_requirements"):
            fp = self.root / BRIEF_FILES[key]
            if fp.is_file():
                parts.append(fp.read_text(encoding="utf-8", errors="replace"))
        return "\n\n".join(parts)

    def has(self, key: str) -> bool:
        return (self.root / BRIEF_FILES[key]).is_file()

    def artifacts(self) -> list[str]:
        out = []
        for r in self.history:
            out.extend(a.path for a in r.artifacts)
        return sorted(set(out))

    def open_criticals(self) -> list:
        """CRITICAL findings from the most recent report of each agent."""
        latest: dict[str, AgentResult] = {}
        for r in self.history:
            latest[r.agent] = r
        out = []
        for r in latest.values():
            out.extend(r.critical_findings)
        return out

    def blockers(self) -> list[str]:
        rows = []
        for t in self.graph.tasks.values():
            if t.status is Status.BLOCKED and t.result:
                rows.extend(f"{t.id}: {b}" for b in t.result.blocked_by)
            elif t.status is Status.FAILED and t.result:
                note = t.result.notes[-1] if t.result.notes else t.result.summary
                rows.append(f"{t.id}: {note[:140]}")
        for t in self.graph.blocked():
            rows.append(f"{t.id}: unreachable (upstream failure)")
        return rows

    def record(self, result: AgentResult) -> None:
        self.history.append(result)

    def advance_phase(self) -> None:
        """Move to the earliest phase that still has unfinished tasks."""
        by_phase = self.graph.by_phase()
        for ph in PHASES:
            tasks = by_phase.get(ph, [])
            if tasks and not all(t.done for t in tasks):
                self.phase = ph
                return
        self.phase = "done"

    def cost(self) -> dict[str, int]:
        return {
            "input_tokens": sum(r.input_tokens for r in self.history),
            "output_tokens": sum(r.output_tokens for r in self.history),
            "tool_calls": sum(r.tool_calls for r in self.history),
            "agent_runs": len(self.history),
        }
