"""Task deduplication and the run ledger.

Rate-limit windows are the budget now, so the cheapest model call is the one we
do not make. Three mechanisms, in order of how much they save:

1. **Dedup.** A task is fingerprinted over everything that could change its
   output -- the specialist's spec, the tier it would run at, the objective,
   the context slice, and the *content* of every required input. If a previous
   run with that exact fingerprint completed and its artifacts are still on
   disk unmodified, the recorded result is replayed and no model runs. This is
   what makes `run` resumable after a crash without redoing finished work.

2. **Invalidation that is actually correct.** Because inputs are hashed by
   content rather than mtime, editing `AGENT/problem_statement.md` invalidates
   every downstream task that read it, and touching a file changes nothing.
   The orchestrator's retry path appends the failure reason to the context,
   which changes the fingerprint -- so retries are never served from cache.

3. **Prompt-cache friendliness.** `stable_prefix` exists so the caller can put
   the unchanging half of a prompt first. Claude Code caches the prefix of each
   request; anything volatile placed early throws that away silently.

The ledger is a plain JSON file under the project, not a global cache. A
hackathon project is self-contained and must stay copyable.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .handoff import AgentResult, Status

LEDGER_FILE = "AGENT/cache/ledger.json"
FORMAT = 2


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def file_digest(path: Path) -> str:
    """Content hash of a file, or a marker for one that is not there."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "absent"


def spec_digest(spec) -> str:
    """Everything about a specialist that could change what it produces.

    Mission prose is included deliberately: editing a prompt should invalidate
    the work done under the old one, or the cache would hide the very change
    the author is trying to test.
    """
    return _sha(
        json.dumps(
            {
                "name": spec.name,
                "mission": spec.mission,
                "tools": sorted(spec.tools),
                "write_paths": list(spec.write_paths),
                "requires": list(spec.requires),
                "produces": list(spec.produces),
            },
            sort_keys=True,
        )
    )[:16]


def fingerprint(spec, tier, root: Path, objective: str, context: str) -> str:
    """The identity of one unit of work."""
    inputs = {rel: file_digest(root / rel) for rel in sorted(spec.requires)}
    return _sha(
        json.dumps(
            {
                "format": FORMAT,
                "spec": spec_digest(spec),
                "model": tier.model,
                "effort": tier.effort,
                "objective": objective.strip(),
                "context": context.strip(),
                "inputs": inputs,
            },
            sort_keys=True,
        )
    )[:24]


@dataclass
class Entry:
    fingerprint: str
    agent: str
    task_id: str
    model: str
    tier: str
    completed_at: float
    artifacts: dict[str, str]        # produced path -> content digest
    result: dict                      # a serialised AgentResult

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint, "agent": self.agent,
            "task_id": self.task_id, "model": self.model, "tier": self.tier,
            "completed_at": self.completed_at, "artifacts": self.artifacts,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(
            fingerprint=d["fingerprint"], agent=d.get("agent", ""),
            task_id=d.get("task_id", ""), model=d.get("model", ""),
            tier=d.get("tier", ""), completed_at=d.get("completed_at", 0.0),
            artifacts=d.get("artifacts", {}), result=d.get("result", {}),
        )

    def artifacts_intact(self, root: Path) -> bool:
        """True only if every recorded artifact is still there, byte-identical."""
        return all(file_digest(root / rel) == digest
                   for rel, digest in self.artifacts.items())


@dataclass
class Ledger:
    """Persistent record of completed work, keyed by fingerprint."""

    root: Path
    entries: dict[str, Entry] = field(default_factory=dict)
    enabled: bool = True
    hits: int = 0
    misses: int = 0

    @classmethod
    def load(cls, root: Path, *, enabled: bool = True) -> "Ledger":
        led = cls(root=Path(root), enabled=enabled)
        path = led.path
        if not path.is_file():
            return led
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return led  # a corrupt ledger costs a re-run, never a crash
        if raw.get("format") != FORMAT:
            return led  # fingerprint scheme changed; start clean
        for d in raw.get("entries", []):
            try:
                entry = Entry.from_dict(d)
            except KeyError:
                continue
            led.entries[entry.fingerprint] = entry
        return led

    @property
    def path(self) -> Path:
        return self.root / LEDGER_FILE

    def save(self) -> None:
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "format": FORMAT,
                    "updated": time.time(),
                    "entries": [e.to_dict() for e in self.entries.values()],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- the two calls the runner makes ------------------------------------

    def lookup(self, fp: str) -> AgentResult | None:
        """A replayable result for this exact work, or None."""
        if not self.enabled:
            return None
        entry = self.entries.get(fp)
        if entry is None or not entry.artifacts_intact(self.root):
            self.misses += 1
            return None
        try:
            result = AgentResult.from_dict(entry.result)
        except (KeyError, TypeError, ValueError):
            self.misses += 1
            return None
        self.hits += 1
        result.notes = list(result.notes) + [
            f"served from the task ledger (first produced by {entry.agent} "
            f"on {entry.model}); no model call was made"
        ]
        return result

    def record(self, fp: str, spec, tier, result: AgentResult) -> None:
        """Remember completed work. Anything short of completed is not cached."""
        if not self.enabled or result.status is not Status.COMPLETED:
            return
        self.entries[fp] = Entry(
            fingerprint=fp,
            agent=spec.name,
            task_id=result.task_id,
            model=tier.model,
            tier=tier.name,
            completed_at=time.time(),
            artifacts={rel: file_digest(self.root / rel) for rel in spec.produces},
            result=result.to_dict(),
        )
        self.save()

    def stats(self) -> str:
        total = self.hits + self.misses
        if not self.enabled:
            return "ledger disabled"
        if not total:
            return f"ledger: {len(self.entries)} entries, no lookups"
        return (f"ledger: {self.hits}/{total} tasks replayed from cache, "
                f"{len(self.entries)} entries")


def stable_prefix(parts: list[str]) -> str:
    """Join prompt sections so the invariant ones come first.

    Claude Code caches request prefixes. Putting a timestamp, a task id, or a
    retry note near the top invalidates the cache for everything after it, and
    does so silently. Callers pass sections already ordered stable-to-volatile;
    this exists to make that ordering an explicit, greppable decision.
    """
    return "\n\n".join(p.strip() for p in parts if p and p.strip())
