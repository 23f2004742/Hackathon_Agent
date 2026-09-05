"""Project-memory and cross-hackathon-reuse tools.

These implement the two outer memory layers: the shared knowledge base at
hackathon/.knowledge/ and the per-project log at <project>/AGENT/. Task-level
context is assembled separately (see context.py) and never lives in a tool.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .base import fail, guard, tool, truncate

DECISIONS = "AGENT/decision_log.md"
REFERENCES = "AGENT/reference_decisions.md"


def knowledge_root(project_root: Path) -> Path:
    """hackathon/.knowledge lives above the project folder, and is READ-ONLY
    to specialists -- only the postmortem step writes to it."""
    return project_root.parent / ".knowledge"


@tool("knowledge")
def knowledge_search(query: str, max_results: int = 12) -> str:
    """Search the cross-hackathon knowledge base for prior art and lessons.

    ALWAYS call this before building a component from scratch. It searches
    patterns.md and index.json across every past hackathon. If it returns a
    reusable component, adapt it and log that with record_reuse rather than
    rebuilding.

    Args:
        query: What you are about to build or decide, in a few words.
        max_results: Cap on matching lines returned. Default 12.
    """
    try:
        ctx = guard("knowledge_search")
        kroot = knowledge_root(ctx.root)
        if not kroot.is_dir():
            return "no shared knowledge base found (expected ../.knowledge)"
        terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 3]
        hits: list[str] = []

        pat = kroot / "patterns.md"
        if pat.is_file():
            block: list[str] = []
            for line in pat.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("**") and block:
                    joined = " ".join(block)
                    if any(t in joined.lower() for t in terms):
                        hits.append("patterns.md :: " + truncate(joined, 320))
                    block = [line]
                else:
                    block.append(line)
            if block:
                joined = " ".join(block)
                if any(t in joined.lower() for t in terms):
                    hits.append("patterns.md :: " + truncate(joined, 320))

        idx = kroot / "index.json"
        if idx.is_file():
            try:
                data = json.loads(idx.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            for proj in data.get("projects", []):
                blob = json.dumps(proj).lower()
                if not any(t in blob for t in terms):
                    continue
                comps = "; ".join(
                    f"{c['path']} - {c['what']}" for c in proj.get("reusable_components", [])
                )
                hits.append(
                    f"index.json :: {proj['name']} ({proj.get('title','')})\n"
                    f"    location: {proj.get('location','?')}\n"
                    f"    reusable: {comps or 'none listed'}"
                )

        if not hits:
            return f"no prior art matching '{query}'. Build it new, and it will be indexed after this hackathon."
        return truncate("\n\n".join(hits[:max_results]))
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("knowledge", writes=True)
def record_reuse(component: str, source: str, used_for: str, changes: str, status: str = "Integrated") -> str:
    """Log that you adapted something from a previous hackathon.

    Call this whenever prior work materially shaped what you built. The
    reference log is a deliverable, not bookkeeping -- it is how the knowledge
    base compounds.

    Args:
        component: What you reused, e.g. "hackathon_04/agent/tool_executor.py".
        source: Which previous hackathon it came from.
        used_for: What you used it for in this project.
        changes: What you had to change to make it fit.
        status: Integrated, Adapted, Rejected or Evaluated. Default Integrated.
    """
    try:
        ctx = guard("record_reuse")
        p = ctx.root / REFERENCES
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("# Reference Decisions\n\nPrior work that materially shaped this project.\n", encoding="utf-8")
        entry = (
            f"\n---\n\n## {component}\n\n"
            f"- **Source:** {source}\n"
            f"- **Used for:** {used_for}\n"
            f"- **Changes:** {changes}\n"
            f"- **Status:** {status}\n"
            f"- **Logged by:** {ctx.agent} on {date.today().isoformat()}\n"
        )
        if not ctx.dry_run:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(entry)
        return f"logged reuse of {component} ({status})"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("knowledge", writes=True)
def record_decision(what: str, why: str, alternatives: str = "", reversible: bool = True) -> str:
    """Record a consequential decision in the project decision log.

    Record anything a later specialist would be confused by, or that you would
    need to revisit if an assumption broke. Cheap to write, expensive to lack.

    Args:
        what: The decision, in one sentence.
        why: The reasoning, including what you traded away.
        alternatives: Options you rejected, comma-separated.
        reversible: False if undoing this would cost significant hackathon time.
    """
    try:
        ctx = guard("record_decision")
        p = ctx.root / DECISIONS
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("# Decision Log\n", encoding="utf-8")
        entry = (
            f"\n## {what}\n\n"
            f"- **Why:** {why}\n"
            f"- **Alternatives:** {alternatives or 'none recorded'}\n"
            f"- **Reversible:** {'yes' if reversible else 'NO - revisiting costs real time'}\n"
            f"- **By:** {ctx.agent} on {date.today().isoformat()}\n"
        )
        if not ctx.dry_run:
            with p.open("a", encoding="utf-8") as fh:
                fh.write(entry)
        return f"recorded decision: {what}"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("knowledge")
def read_decisions(filter_text: str = "") -> str:
    """Read the project decision log, to avoid contradicting earlier choices.

    Args:
        filter_text: Only return sections containing this text.
    """
    try:
        ctx = guard("read_decisions")
        p = ctx.root / DECISIONS
        if not p.is_file():
            return "no decisions recorded yet"
        text = p.read_text(encoding="utf-8", errors="replace")
        if filter_text:
            blocks = [b for b in text.split("\n## ") if filter_text.lower() in b.lower()]
            return truncate("\n## ".join(blocks)) if blocks else f"no decisions match '{filter_text}'"
        return truncate(text)
    except Exception as e:  # noqa: BLE001
        return fail(e)
