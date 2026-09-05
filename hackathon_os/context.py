"""Targeted context retrieval.

Three memory layers, per the brief:

  global   hackathon/.knowledge/     lessons and components across hackathons
  project  <project>/AGENT/          decisions and state for this hackathon
  task     assembled here            only what this specialist needs now

Each specialist declares `context_keys`; only those slices are assembled. A
slice is a *digest* -- headings plus a bounded excerpt -- not the whole file,
because a specialist that receives six full documents spends its attention on
reading rather than working. If it needs the detail it has read_file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# key -> (heading, source files in preference order)
SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "problem": ("Problem statement", ("AGENT/problem_statement.md",)),
    "constraints": ("Constraints", ("AGENT/constraints.md",)),
    "judging": ("Judging criteria", ("AGENT/judging_criteria.md",)),
    "submission": ("Submission requirements", ("AGENT/submission_requirements.md",)),
    "requirements": ("Requirements", ("PRODUCT/requirements.md",)),
    "product_plan": ("Product plan / scope", ("PRODUCT/product_plan.md",)),
    "strategy": ("Strategy", ("PRODUCT/strategy.md",)),
    "architecture": ("Architecture", ("PRODUCT/architecture.md",)),
    "research": (
        "Research digest",
        ("RESEARCH/market_report.md", "RESEARCH/competitive_analysis.md",
         "RESEARCH/technical_research.md", "RESEARCH/user_research.md"),
    ),
    "design": ("Design specs", ("DESIGN/ux.md", "DESIGN/ui.md", "DESIGN/brand.md")),
    "test_results": (
        "Validation results",
        ("VALIDATION/test_report.md", "VALIDATION/ml_eval.md",
         "VALIDATION/security_review.md"),
    ),
}

BUDGET_PER_SLICE = 1400   # characters
BUDGET_TOTAL = 9000


@dataclass
class ContextBuilder:
    root: Path
    budget: int = BUDGET_TOTAL

    # -- digesting -------------------------------------------------------

    def _digest(self, rel: str, limit: int) -> str | None:
        p = self.root / rel
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        if len(text) <= limit:
            return text
        # Keep the shape (headings) and the opening of each section.
        out, budget_left = [], limit
        blocks = re.split(r"\n(?=#{1,3} )", text)
        for b in blocks:
            if budget_left <= 0:
                break
            take = b[: min(len(b), max(220, budget_left // max(1, len(blocks))))]
            out.append(take.rstrip())
            budget_left -= len(take)
        return "\n\n".join(out) + f"\n\n[digest of {rel}; call read_file for the full document]"

    def slice_for(self, key: str, limit: int = BUDGET_PER_SLICE) -> str | None:
        if key == "prior_art":
            return self._prior_art(limit)
        entry = SOURCES.get(key)
        if not entry:
            return None
        heading, files = entry
        parts = []
        per = max(400, limit // max(1, len(files)))
        for rel in files:
            d = self._digest(rel, per)
            if d:
                parts.append(f"### {rel}\n\n{d}")
        if not parts:
            return None
        return f"## {heading}\n\n" + "\n\n".join(parts)

    def _prior_art(self, limit: int) -> str | None:
        """The global layer: what previous hackathons offer this project."""
        kroot = self.root.parent / ".knowledge"
        idx = kroot / "index.json"
        if not idx.is_file():
            return None
        try:
            data = json.loads(idx.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        lines = ["## Prior art available (cross-hackathon knowledge base)", ""]
        for proj in data.get("projects", []):
            comps = proj.get("reusable_components", [])
            lines.append(f"- **{proj['name']}** ({', '.join(proj.get('domains', [])[:3])})")
            for c in comps[:3]:
                lines.append(f"    - `{c['path']}` — {c['what']}")
        lines.append("")
        lines.append("Call knowledge_search before building anything from scratch.")
        return "\n".join(lines)[:limit]

    # -- assembly --------------------------------------------------------

    def build(self, keys: tuple[str, ...], *, recent: list | None = None) -> str:
        parts: list[str] = []
        spent = 0
        for key in keys:
            if spent >= self.budget:
                break
            s = self.slice_for(key, min(BUDGET_PER_SLICE, self.budget - spent))
            if s:
                parts.append(s)
                spent += len(s)
        if recent:
            parts.append(self._recent(recent))
        if not parts:
            return ""
        return "# Context\n\n" + "\n\n---\n\n".join(p for p in parts if p)

    @staticmethod
    def _recent(results: list) -> str:
        """What the specialists immediately upstream reported."""
        rows = ["## What just happened upstream", ""]
        for r in results[-5:]:
            status = r.status.value if hasattr(r.status, "value") else r.status
            rows.append(f"- **{r.agent}** [{status}]: {(r.summary or '').strip()[:280]}")
            for f in getattr(r, "findings", [])[:2]:
                sev = f.severity.value if hasattr(f.severity, "value") else f.severity
                if sev in ("CRITICAL", "HIGH"):
                    rows.append(f"    - {sev}: {f.summary[:180]}")
        return "\n".join(rows)
