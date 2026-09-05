"""Token and context optimisation.

On a subscription the currency is rate-limit windows, so the cheapest token is
the one never sent. This module is the layer that decides what actually
reaches a specialist, and it does four things the raw `ContextBuilder` did not:

1. **Prioritise.** Every piece of candidate context carries a priority band
   (see `Priority`). When the budget is tight the lowest bands are dropped
   first, and P1 -- what the task literally requires -- is never dropped.

2. **Deduplicate.** The same paragraph routinely appears in the state, a
   handoff, an artifact digest and the knowledge base. It is sent once, at the
   highest priority that carries it, and the copies are removed.

3. **Compress, not truncate.** An oversized artifact is reduced by *keeping*
   the lines that carry decisions, interfaces, constraints, file paths, errors,
   unresolved issues and acceptance criteria, and dropping the prose around
   them. Blind truncation loses the acceptance criteria at the bottom of the
   document, which is exactly the part a downstream specialist needed.

4. **Point at files rather than inlining them.** A specialist gets a targeted
   excerpt and a reminder that `read_file` exists. Pushing a 40KB module into
   every prompt costs the window whether or not the agent reads it.

Token counting is local and deliberately approximate -- an estimator, not a
tokenizer. Requiring a paid counting API to save subscription usage would be a
strange trade, and the numbers are used for budgeting and reporting, where a
few per cent of error changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------

_WORDS = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str) -> int:
    """Approximate the token count of `text` with no external service.

    Claude's tokenizer sits between "one token per word" and "one token per
    four characters" for English prose, and closer to the character bound for
    code and JSON. Taking the larger of the two estimates errs towards
    over-reserving budget, which is the safe direction: an over-estimate spends
    a little less of the window than it could, an under-estimate blows it.
    """
    if not text:
        return 0
    by_char = len(text) / 3.8
    by_word = len(_WORDS.findall(text)) * 0.78
    return int(max(by_char, by_word)) + 1


def estimate_all(*texts: str) -> int:
    return sum(estimate_tokens(t) for t in texts)


# ---------------------------------------------------------------------------
# Priority bands
# ---------------------------------------------------------------------------


class Priority:
    """The order in which context is sacrificed when the budget bites."""

    TASK = 1            # what this task literally requires
    DEPENDENCY = 2      # artifacts this task directly depends on
    ARTIFACT = 3        # other relevant artifacts produced so far
    STATE = 4           # current project state, upstream handoffs
    KNOWLEDGE = 5       # relevant knowledge-base results
    HISTORY = 6         # prior hackathons
    BACKGROUND = 7      # general background

    NAMES = {
        1: "task requirements", 2: "direct dependencies", 3: "relevant artifacts",
        4: "project state", 5: "knowledge", 6: "prior hackathons", 7: "background",
    }

    #: Never dropped, however tight the budget. Sending a specialist a task it
    #: cannot understand costs a whole wasted run, which is worse than any
    #: saving here.
    PROTECTED = (1,)


# Which band each context key belongs to. A key naming a file the specialist
# declares in `requires` is promoted to DEPENDENCY by `key_priority`.
KEY_PRIORITY: dict[str, int] = {
    "problem": Priority.TASK,
    "constraints": Priority.TASK,
    "judging": Priority.DEPENDENCY,
    "submission": Priority.DEPENDENCY,
    "requirements": Priority.ARTIFACT,
    "product_plan": Priority.ARTIFACT,
    "architecture": Priority.ARTIFACT,
    "strategy": Priority.ARTIFACT,
    "design": Priority.ARTIFACT,
    "research": Priority.ARTIFACT,
    "test_results": Priority.ARTIFACT,
    "prior_art": Priority.HISTORY,
    "upstream": Priority.STATE,
    "knowledge": Priority.KNOWLEDGE,
}

# key -> the artifact paths it digests, so a key can be promoted when the
# specialist declares one of them as a required input.
KEY_FILES: dict[str, tuple[str, ...]] = {
    "requirements": ("PRODUCT/requirements.md",),
    "product_plan": ("PRODUCT/product_plan.md",),
    "architecture": ("PRODUCT/architecture.md",),
    "strategy": ("PRODUCT/strategy.md",),
    "design": ("DESIGN/ux.md", "DESIGN/ui.md", "DESIGN/brand.md"),
    "test_results": ("VALIDATION/test_report.md", "VALIDATION/ml_eval.md",
                     "VALIDATION/security_review.md"),
    "problem": ("AGENT/problem_statement.md",),
    "constraints": ("AGENT/constraints.md",),
    "judging": ("AGENT/judging_criteria.md",),
    "submission": ("AGENT/submission_requirements.md",),
}


def key_priority(key: str, requires: tuple[str, ...] = ()) -> int:
    """The band for one context key, given what the specialist requires."""
    base = KEY_PRIORITY.get(key, Priority.BACKGROUND)
    if any(f in requires for f in KEY_FILES.get(key, ())):
        return min(base, Priority.DEPENDENCY)
    return base


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


@dataclass
class Budget:
    """How much this one task is allowed to spend, in estimated tokens."""

    context: int = 3_500
    output: int = 8_000
    research: int = 2_000
    complexity: int = 3      # 1..5, what the budget was sized against
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "context_budget": self.context, "output_budget": self.output,
            "research_budget": self.research, "complexity": self.complexity,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Budget":
        return cls(
            context=int(d.get("context_budget", 3_500)),
            output=int(d.get("output_budget", 8_000)),
            research=int(d.get("research_budget", 2_000)),
            complexity=int(d.get("complexity", 3)),
            reason=str(d.get("reason", "")),
        )


#: Roles whose output is inherently long -- a deck, a full audit, a code file.
_WIDE_OUTPUT = {
    "architect", "final_auditor", "ml_engineer", "ai_engineer",
    "backend_engineer", "frontend_engineer", "technical_writer",
    "presentation_builder", "requirements_analyst",
}
#: Roles that genuinely need breadth of input to do their job at all.
_WIDE_INPUT = {"architect", "final_auditor", "requirements_auditor",
               "pitch_strategist", "developer", "submission_manager"}


def budget_for(spec, task=None, *, project_complexity: int = 3) -> Budget:
    """Size the budgets for one task, from its own shape rather than a constant.

    Complexity is the task's declared effort and priority combined with the
    project's own complexity, because a CRITICAL task in a complex project is
    where an under-sized context actually costs a re-run.
    """
    effort = getattr(task, "effort", 3) or 3
    impact = getattr(task, "impact", 3) or 3
    prio = getattr(getattr(task, "priority", None), "rank", 2)
    complexity = max(1, min(5, round((effort + impact + project_complexity) / 3 - prio * 0.25)))

    context = 2_200 + complexity * 600
    if spec.name in _WIDE_INPUT:
        context += 1_200
    output = 4_000 + complexity * 1_400
    if spec.name in _WIDE_OUTPUT:
        output += 4_000
    output = min(output, getattr(spec, "max_tokens", 16_000))
    research = 900 + complexity * 500
    if not {"web_search", "knowledge_search"} & set(getattr(spec, "tools", ())):
        research = 0

    return Budget(
        context=context, output=output, research=research, complexity=complexity,
        reason=(f"effort={effort}, impact={impact}, project complexity="
                f"{project_complexity} -> complexity {complexity}"),
    )


# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------


@dataclass
class ContextItem:
    """One candidate slice of context, before the optimiser has ruled on it."""

    key: str
    body: str
    priority: int = Priority.BACKGROUND
    source: str = ""

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.body)


@dataclass
class OptimizerMetrics:
    """The numbers `hackathon status` reports. Names are part of the contract."""

    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    context_tokens_removed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    dropped_items: int = 0
    deduped_blocks: int = 0
    compressed_items: int = 0
    optimised_tasks: int = 0

    @property
    def context_compression_ratio(self) -> float:
        """Fraction of candidate context that survived optimisation.

        1.0 means nothing was removed; 0.4 means 60% of what a naive builder
        would have sent was dropped, deduplicated or compressed away.
        """
        raw = self.estimated_input_tokens + self.context_tokens_removed
        if raw <= 0:
            return 1.0
        return round(self.estimated_input_tokens / raw, 3)

    def merge(self, other: "OptimizerMetrics") -> None:
        self.estimated_input_tokens += other.estimated_input_tokens
        self.estimated_output_tokens += other.estimated_output_tokens
        self.context_tokens_removed += other.context_tokens_removed
        self.cache_hits += other.cache_hits
        self.cache_misses += other.cache_misses
        self.dropped_items += other.dropped_items
        self.deduped_blocks += other.deduped_blocks
        self.compressed_items += other.compressed_items
        self.optimised_tasks += other.optimised_tasks

    def to_dict(self) -> dict:
        d = {
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "context_tokens_removed": self.context_tokens_removed,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "dropped_items": self.dropped_items,
            "deduped_blocks": self.deduped_blocks,
            "compressed_items": self.compressed_items,
            "optimised_tasks": self.optimised_tasks,
        }
        d["context_compression_ratio"] = self.context_compression_ratio
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "OptimizerMetrics":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: int(v) for k, v in (d or {}).items() if k in known})


@dataclass
class OptimizedContext:
    """The result: the text to send, and what it cost to get there."""

    text: str = ""
    budget: Budget = field(default_factory=Budget)
    metrics: OptimizerMetrics = field(default_factory=OptimizerMetrics)
    kept: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (key, why)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    def explain(self) -> str:
        rows = [f"context: ~{self.tokens} tok of {self.budget.context} budget",
                f"kept: {', '.join(self.kept) or 'nothing'}"]
        if self.dropped:
            rows.append("dropped: " + ", ".join(f"{k} ({w})" for k, w in self.dropped))
        return " | ".join(rows)


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------

#: Lines matching these carry information a downstream specialist cannot
#: reconstruct, so they survive compression whatever the budget.
KEEP_PATTERNS = (
    r"^#{1,6}\s",                                   # structure
    r"\b(decision|decided|chose|rejected|trade-?off)\b",
    r"\b(constraint|must|must not|required|forbidden|deadline)\b",
    r"\b(acceptance criteri|definition of done|pass/fail|success criteri)\b",
    r"\b(interface|endpoint|signature|schema|contract|api)\b",
    r"\b(error|failed|failure|exception|traceback|blocked|blocker)\b",
    r"\b(todo|fixme|open question|unresolved|risk|assumption)\b",
    r"\b(REQ-|NFR-|AC-)\w*",                    # requirement ids
    r"^\s*(?:def |class |function |const |export |CREATE TABLE)",
    r"^\s*(?:[A-Z]{2,}/|src/|tests/|data/)\S+",     # project file paths
    r"\b\S+\.(?:py|md|json|sql|html|ts|tsx|js|yml|yaml|toml|pptx|zip)\b",
    r"^\s*[-*]\s+\*\*",                             # bolded bullet = a claim
    # An HTTP route is an interface even when the surrounding prose never uses
    # the word. Losing one costs the next specialist a guess it will get wrong.
    r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD)\s+/",
    r"^\s*[-*]\s+`",                                # bullet naming an identifier
)
_KEEP = re.compile("|".join(KEEP_PATTERNS), re.IGNORECASE)


def compress(text: str, limit_tokens: int, *, label: str = "") -> tuple[str, bool]:
    """Reduce `text` to roughly `limit_tokens`, keeping what matters.

    Returns (text, was_compressed). The strategy is subtractive rather than
    positional: keep every line that carries a decision, interface, constraint,
    path, error, unresolved issue or acceptance criterion, then spend whatever
    budget remains on the opening lines of each section. What is dropped is
    connective prose, which the reader can always recover with `read_file`.
    """
    if estimate_tokens(text) <= limit_tokens:
        return text, False

    lines = text.splitlines()
    must = [i for i, ln in enumerate(lines) if ln.strip() and _KEEP.search(ln)]
    kept: set[int] = set()
    spent = 0
    for i in must:
        cost = estimate_tokens(lines[i])
        if spent + cost > limit_tokens:
            break
        kept.add(i)
        spent += cost

    # Fill the remainder with the first lines after each kept heading, so the
    # result reads as a document rather than a list of fragments.
    for i, ln in enumerate(lines):
        if spent >= limit_tokens:
            break
        if i in kept or not ln.strip():
            continue
        if (i - 1) in kept or (i - 2) in kept:
            cost = estimate_tokens(ln)
            if spent + cost > limit_tokens:
                break
            kept.add(i)
            spent += cost

    out: list[str] = []
    last = -2
    for i in sorted(kept):
        if i > last + 1:
            out.append("...")
        out.append(lines[i])
        last = i
    tail = f"[compressed{' ' + label if label else ''}; call read_file for the full text]"
    return "\n".join(out).strip() + "\n\n" + tail, True


def excerpt_file(path: Path, keywords: tuple[str, ...], limit_tokens: int) -> str:
    """A targeted excerpt of one file rather than the whole thing.

    Sections whose text matches the task's keywords come first; the rest is
    compressed. This is the file-aware half of the optimiser: an agent gets a
    pointer and the relevant part, and fetches the rest itself if it needs to.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.strip():
        return ""
    blocks = re.split(r"\n(?=#{1,3} )", text)
    terms = tuple(k.lower() for k in keywords if len(k) > 3)
    scored = []
    for i, b in enumerate(blocks):
        low = b.lower()
        score = sum(low.count(t) for t in terms)
        scored.append((-score, i, b))
    scored.sort()
    out: list[str] = []
    spent = 0
    for _neg, _i, b in scored:
        cost = estimate_tokens(b)
        if spent + cost > limit_tokens:
            piece, _ = compress(b, max(60, limit_tokens - spent))
            if piece.strip():
                out.append(piece)
            break
        out.append(b)
        spent += cost
    return "\n\n".join(out).strip()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

_DEDUP_FLOOR = 60      # characters; below this a repeat is not worth removing


def _normalise(block: str) -> str:
    return re.sub(r"\s+", " ", block.strip().lower())


def _block_hash(block: str) -> str:
    return hashlib.sha1(_normalise(block).encode("utf-8", "replace")).hexdigest()[:16]


def dedupe(body: str, seen: set[str]) -> tuple[str, int]:
    """Remove paragraphs already present in higher-priority context.

    Returns (body, blocks_removed). `seen` is mutated, so callers walk items in
    priority order and the highest-priority copy of a fact is the one kept.
    """
    blocks = re.split(r"\n\s*\n", body)
    out: list[str] = []
    removed = 0
    for b in blocks:
        if len(b.strip()) < _DEDUP_FLOOR:
            out.append(b)
            continue
        h = _block_hash(b)
        if h in seen:
            removed += 1
            continue
        seen.add(h)
        out.append(b)
    return "\n\n".join(x for x in out if x.strip()), removed


# ---------------------------------------------------------------------------
# The summary cache -- do not pay twice for the same compression
# ---------------------------------------------------------------------------

CACHE_FILE = "AGENT/cache/context_cache.json"


@dataclass
class SummaryCache:
    """Content-addressed store of compressions and reusable research.

    Keyed by content hash and target size, so it is correct by construction:
    edit the artifact and the key changes. It exists so that the same 40KB
    architecture document is not re-summarised for each of the eight
    specialists that depend on it.
    """

    root: Path
    entries: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    hits: int = 0
    misses: int = 0
    dirty: bool = False

    @classmethod
    def load(cls, root: Path, *, enabled: bool = True) -> "SummaryCache":
        c = cls(root=Path(root), enabled=enabled)
        p = c.path
        if not p.is_file():
            return c
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return c
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            c.entries = {str(k): str(v) for k, v in raw["entries"].items()}
        return c

    @property
    def path(self) -> Path:
        return self.root / CACHE_FILE

    @staticmethod
    def key(kind: str, content: str, limit: int) -> str:
        h = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:20]
        return f"{kind}:{limit}:{h}"

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        hit = self.entries.get(key)
        if hit is None:
            self.misses += 1
            return None
        self.hits += 1
        return hit

    def put(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        self.entries[key] = value
        self.dirty = True

    def save(self) -> None:
        if not self.enabled or not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"entries": self.entries}, indent=2), encoding="utf-8"
        )
        self.dirty = False


# ---------------------------------------------------------------------------
# The optimiser
# ---------------------------------------------------------------------------


class TokenOptimizer:
    """Turns candidate context into the smallest thing that still works."""

    def __init__(self, root: Path, *, cache: bool = True) -> None:
        self.root = Path(root)
        self.cache = SummaryCache.load(self.root, enabled=cache)
        self.metrics = OptimizerMetrics()

    # -- the one call the orchestrator makes -----------------------------

    def optimize(
        self,
        items: list[ContextItem],
        budget: Budget,
        *,
        header: str = "# Context",
    ) -> OptimizedContext:
        """Prioritise, deduplicate, compress and trim to budget."""
        result = OptimizedContext(budget=budget)
        m = OptimizerMetrics(optimised_tasks=1)
        hits_before, misses_before = self.cache.hits, self.cache.misses

        raw_tokens = sum(i.tokens for i in items)
        ordered = sorted(
            [i for i in items if i.body and i.body.strip()],
            key=lambda i: (i.priority, -i.tokens),
        )

        seen: set[str] = set()
        kept: list[ContextItem] = []
        spent = 0
        for item in ordered:
            body, removed = dedupe(item.body, seen)
            m.deduped_blocks += removed
            if not body.strip():
                result.dropped.append((item.key, "entirely duplicated"))
                m.dropped_items += 1
                continue

            remaining = budget.context - spent
            if remaining <= 120:
                # Out of room. Protected bands are never dropped; everything
                # else here is genuinely optional.
                if item.priority in Priority.PROTECTED:
                    remaining = 400
                else:
                    result.dropped.append(
                        (item.key, f"budget exhausted (P{item.priority} "
                                   f"{Priority.NAMES.get(item.priority, '?')})")
                    )
                    m.dropped_items += 1
                    continue

            share = self._share(item, remaining, budget)
            if estimate_tokens(body) > share:
                key = SummaryCache.key(item.key, body, share)
                cached = self.cache.get(key)
                if cached is not None:
                    body = cached
                    m.compressed_items += 1
                else:
                    body, changed = compress(body, share, label=item.source or item.key)
                    self.cache.put(key, body)
                    if changed:
                        m.compressed_items += 1

            kept.append(ContextItem(item.key, body, item.priority, item.source))
            spent += estimate_tokens(body)
            result.kept.append(item.key)

        text = (header + "\n\n" + "\n\n---\n\n".join(i.body for i in kept)) if kept else ""
        result.text = text
        m.estimated_input_tokens = estimate_tokens(text)
        m.context_tokens_removed = max(0, raw_tokens - m.estimated_input_tokens)
        m.estimated_output_tokens = budget.output
        m.cache_hits = self.cache.hits - hits_before
        m.cache_misses = self.cache.misses - misses_before
        result.metrics = m
        self.metrics.merge(m)
        self.cache.save()
        return result

    @staticmethod
    def _share(item: ContextItem, remaining: int, budget: Budget) -> int:
        """How much of what is left this item may take.

        High-priority items may take most of the remaining budget; low-priority
        ones are capped so that a chatty knowledge-base result cannot crowd out
        the artifact the task actually depends on.
        """
        caps = {
            Priority.TASK: 1.0, Priority.DEPENDENCY: 0.65, Priority.ARTIFACT: 0.45,
            Priority.STATE: 0.3, Priority.KNOWLEDGE: 0.25, Priority.HISTORY: 0.2,
            Priority.BACKGROUND: 0.15,
        }
        cap = caps.get(item.priority, 0.2)
        return max(150, int(min(remaining, budget.context * cap)))

    # -- reuse -----------------------------------------------------------

    def reuse(self, kind: str, query: str) -> str | None:
        """A previously computed research/analysis result for this exact query."""
        return self.cache.get(SummaryCache.key(f"reuse:{kind}", query.strip().lower(), 0))

    def remember(self, kind: str, query: str, value: str) -> None:
        self.cache.put(SummaryCache.key(f"reuse:{kind}", query.strip().lower(), 0), value)
        self.cache.save()

    def absorb_ledger(self, ledger) -> None:
        """Fold the task ledger's hit/miss counters into the reported metrics."""
        if ledger is None:
            return
        self.metrics.cache_hits += int(getattr(ledger, "hits", 0) or 0)
        self.metrics.cache_misses += int(getattr(ledger, "misses", 0) or 0)
