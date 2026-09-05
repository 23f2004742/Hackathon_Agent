"""The one tool every specialist gets: how it reports back.

Making the handoff a *tool call* rather than parsed prose is what keeps
coordination reliable. The model fills a typed schema the SDK enforces, so the
Orchestrator never has to guess what an agent meant.
"""

from __future__ import annotations

import json

from .base import fail, guard, tool


@tool("handoff", writes=True)
def submit_handoff(
    status: str,
    summary: str,
    findings_json: str = "[]",
    decisions_json: str = "[]",
    assumptions_json: str = "[]",
    risks_json: str = "[]",
    next_tasks_json: str = "[]",
    blocked_by_json: str = "[]",
) -> str:
    """Report your finished work to the Orchestrator. Call this exactly once, last.

    Do not call this until you have actually written the artifacts your task
    requires -- the runner checks the filesystem and will mark you failed if
    they are absent, regardless of what you report here.

    Args:
        status: One of completed, failed, blocked, needs_human.
        summary: What you did and what the next specialist needs to know. 2-4 sentences.
        findings_json: JSON array of {"summary","severity","evidence","source"}.
            severity is CRITICAL, HIGH, MEDIUM, LOW or INFO.
        decisions_json: JSON array of {"what","why","alternatives":[],"reversible":bool}.
        assumptions_json: JSON array of strings -- anything you assumed rather than verified.
        risks_json: JSON array of strings -- what could still go wrong.
        next_tasks_json: JSON array of {"agent","objective","priority","reason"}.
            Proposals only; the Orchestrator decides what gets scheduled.
        blocked_by_json: JSON array of strings naming what you needed and lacked.
    """
    try:
        ctx = guard("submit_handoff")

        def parse(raw: str, field: str) -> list:
            if not raw or not raw.strip():
                return []
            try:
                v = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"{field} is not valid JSON: {e}") from e
            if not isinstance(v, list):
                raise ValueError(f"{field} must be a JSON array, got {type(v).__name__}")
            return v

        status_l = status.strip().lower()
        valid = {"completed", "failed", "blocked", "needs_human", "skipped"}
        if status_l not in valid:
            return f"status must be one of {sorted(valid)}, got '{status}'"

        ctx.handoff = {
            "status": status_l,
            "summary": summary.strip(),
            "findings": parse(findings_json, "findings_json"),
            "decisions": parse(decisions_json, "decisions_json"),
            "assumptions": parse(assumptions_json, "assumptions_json"),
            "risks": parse(risks_json, "risks_json"),
            "next_tasks": parse(next_tasks_json, "next_tasks_json"),
            "blocked_by": parse(blocked_by_json, "blocked_by_json"),
        }
        return f"handoff received ({status_l}). You are done; stop calling tools."
    except Exception as e:  # noqa: BLE001
        return fail(e)
