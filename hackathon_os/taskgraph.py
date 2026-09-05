"""The dependency-aware task graph.

Agents do not run in a fixed sequence. A task becomes runnable when its
dependencies have succeeded and its required input files exist; everything
runnable at the same moment can run in parallel. Ordering within a wave is by
priority, then by value density (impact / effort), so when the deadline bites
the cheap high-impact work has already happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .handoff import AgentResult, Priority, Status


@dataclass
class Task:
    id: str
    agent: str
    objective: str
    depends_on: tuple[str, ...] = ()
    priority: Priority = Priority.MEDIUM
    impact: int = 3          # 1-5, expected contribution to the score
    effort: int = 3          # 1-5, cost to do it
    phase: str = "build"
    optional: bool = False   # may be skipped under time pressure
    status: Status | None = None
    result: AgentResult | None = None
    started: str = ""
    finished: str = ""
    attempts: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.priority, str):
            self.priority = Priority(self.priority)
        if isinstance(self.status, str):
            self.status = Status(self.status)

    @property
    def done(self) -> bool:
        return self.status in (Status.COMPLETED, Status.SKIPPED)

    @property
    def value_density(self) -> float:
        """Expected hackathon value per unit of effort."""
        return self.impact / max(1, self.effort)

    @property
    def sort_key(self) -> tuple:
        return (self.priority.rank, -self.value_density, self.id)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "agent": self.agent, "objective": self.objective,
            "depends_on": list(self.depends_on), "priority": self.priority.value,
            "impact": self.impact, "effort": self.effort, "phase": self.phase,
            "optional": self.optional,
            "status": self.status.value if self.status else None,
            "started": self.started, "finished": self.finished,
            "attempts": self.attempts,
            "result": self.result.to_dict() if self.result else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        d = dict(d)
        res = d.pop("result", None)
        t = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        t.depends_on = tuple(t.depends_on)
        if res:
            t.result = AgentResult.from_dict(res)
        return t


class CycleError(Exception):
    pass


@dataclass
class TaskGraph:
    tasks: dict[str, Task] = field(default_factory=dict)

    # -- construction ----------------------------------------------------

    def add(self, task: Task) -> Task:
        if task.id in self.tasks:
            raise ValueError(f"duplicate task id: {task.id}")
        unknown = [d for d in task.depends_on if d not in self.tasks]
        if unknown:
            raise ValueError(f"{task.id} depends on unknown task(s): {unknown}")
        self.tasks[task.id] = task
        return task

    def validate(self) -> None:
        """Reject a graph with a cycle before anything runs."""
        colour: dict[str, int] = {}

        def visit(tid: str, path: list[str]) -> None:
            state = colour.get(tid, 0)
            if state == 1:
                raise CycleError(" -> ".join(path + [tid]))
            if state == 2:
                return
            colour[tid] = 1
            for dep in self.tasks[tid].depends_on:
                visit(dep, path + [tid])
            colour[tid] = 2

        for tid in self.tasks:
            visit(tid, [])

    # -- scheduling ------------------------------------------------------

    def ready(self, root: Path | None = None, specs: dict | None = None) -> list[Task]:
        """Tasks whose dependencies succeeded and whose inputs exist.

        Dependency satisfaction is checked twice on purpose: the upstream task
        must have completed, AND the file it was supposed to produce must be on
        disk. An agent that reported success without writing anything does not
        unblock its dependants.
        """
        out = []
        for t in self.tasks.values():
            if t.status is not None:
                continue
            deps = [self.tasks[d] for d in t.depends_on]
            if not all(d.status is Status.COMPLETED or (d.optional and d.done) for d in deps):
                continue
            if root is not None and specs is not None:
                spec = specs.get(t.agent)
                if spec is not None and spec.missing_inputs(root):
                    continue
            out.append(t)
        return sorted(out, key=lambda t: t.sort_key)

    def blocked(self) -> list[Task]:
        """Pending tasks that can never run because an upstream task failed."""
        dead = {
            t.id for t in self.tasks.values()
            if t.status in (Status.FAILED, Status.BLOCKED) and not t.optional
        }
        if not dead:
            return []
        out, changed = [], True
        while changed:
            changed = False
            for t in self.tasks.values():
                if t.status is not None or t.id in dead:
                    continue
                if any(d in dead for d in t.depends_on):
                    dead.add(t.id)
                    out.append(t)
                    changed = True
        return out

    def pending(self) -> list[Task]:
        return [t for t in self.tasks.values() if t.status is None]

    def by_phase(self) -> dict[str, list[Task]]:
        out: dict[str, list[Task]] = {}
        for t in self.tasks.values():
            out.setdefault(t.phase, []).append(t)
        return out

    # -- progress --------------------------------------------------------

    @property
    def progress(self) -> float:
        if not self.tasks:
            return 0.0
        weight = sum(t.impact for t in self.tasks.values())
        done = sum(t.impact for t in self.tasks.values() if t.done)
        return done / weight if weight else 0.0

    def counts(self) -> dict[str, int]:
        c = {"total": len(self.tasks), "pending": 0, "completed": 0,
             "failed": 0, "blocked": 0, "skipped": 0, "needs_human": 0}
        for t in self.tasks.values():
            if t.status is None:
                c["pending"] += 1
            else:
                c[t.status.value] = c.get(t.status.value, 0) + 1
        return c

    def record(self, task: Task, result: AgentResult) -> None:
        task.result = result
        task.status = result.status
        task.attempts += 1
        task.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict:
        return {"tasks": [t.to_dict() for t in self.tasks.values()]}

    @classmethod
    def from_dict(cls, d: dict) -> "TaskGraph":
        g = cls()
        for row in d.get("tasks", []):
            t = Task.from_dict(row)
            g.tasks[t.id] = t
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "TaskGraph":
        if not path.is_file():
            return cls()
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def ascii(self) -> str:
        """Render the graph as an indented dependency tree, for the CLI."""
        roots = [t for t in self.tasks.values() if not t.depends_on]
        children: dict[str, list[Task]] = {}
        for t in self.tasks.values():
            for d in t.depends_on:
                children.setdefault(d, []).append(t)
        seen: set[str] = set()
        lines: list[str] = []

        from .glyphs import G

        def mark(t: Task) -> str:
            return {
                None: G["pending"], Status.COMPLETED: G["ok"], Status.FAILED: G["fail"],
                Status.BLOCKED: G["unreachable"], Status.SKIPPED: G["skip"],
                Status.NEEDS_HUMAN: G["human"],
            }.get(t.status, G["bullet"])

        def walk(t: Task, depth: int) -> None:
            key = (t.id, depth)
            lines.append(f"{'  ' * depth}{mark(t)} {t.id} [{t.agent}] {t.priority.value.lower()}")
            if t.id in seen:
                return
            seen.add(t.id)
            for c in sorted(children.get(t.id, []), key=lambda x: x.sort_key):
                walk(c, depth + 1)

        for r in sorted(roots, key=lambda x: x.sort_key):
            walk(r, 0)
        return "\n".join(lines)
