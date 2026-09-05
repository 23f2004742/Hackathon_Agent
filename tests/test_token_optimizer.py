"""The token optimiser: does it actually send less, and still send enough?

The failure mode this suite exists to catch is the expensive one -- an
optimiser that reports a lovely compression ratio because it dropped the
acceptance criteria the next specialist needed. So every "we removed things"
assertion here is paired with a "and this survived" assertion.
"""

from __future__ import annotations

import pytest

from hackathon_os import agents as roster
from hackathon_os.handoff import Priority
from hackathon_os.llm import SimulatedBackend
from hackathon_os.orchestrator import Orchestrator
from hackathon_os.state import ProjectState
from hackathon_os.taskgraph import Task
from hackathon_os.token_optimizer import (
    Budget, ContextItem, OptimizerMetrics, Priority as P, SummaryCache,
    TokenOptimizer, budget_for, compress, dedupe, estimate_tokens, excerpt_file,
    key_priority,
)


# -- estimation -------------------------------------------------------------


def test_estimation_needs_no_external_service():
    assert estimate_tokens("") == 0
    short, long = estimate_tokens("hello world"), estimate_tokens("hello world " * 100)
    assert 0 < short < long
    # Within a factor of two of the usual rule of thumb is all we need.
    text = "The quick brown fox jumps over the lazy dog. " * 50
    assert len(text) / 8 < estimate_tokens(text) < len(text) / 2


def test_estimation_is_monotonic():
    base = "some project documentation "
    counts = [estimate_tokens(base * n) for n in (1, 5, 20, 100)]
    assert counts == sorted(counts)


# -- deduplication ----------------------------------------------------------


def test_identical_paragraphs_are_sent_once():
    para = ("The system must expose a REST endpoint at /triage that accepts free "
            "text and returns an urgency score between one and five, with an audit "
            "record written for every call it serves.")
    seen: set[str] = set()
    first, removed_a = dedupe(para, seen)
    second, removed_b = dedupe(para, seen)
    assert first == para and removed_a == 0
    assert second.strip() == "" and removed_b == 1


def test_dedup_ignores_whitespace_and_case_differences():
    a = "A decision was made to use SQLite for the demo, because it needs no server process at all."
    b = "  a DECISION was made   to use SQLite for the demo, because it needs no server process at all.  "
    seen: set[str] = set()
    dedupe(a, seen)
    _, removed = dedupe(b, seen)
    assert removed == 1


def test_short_lines_are_never_deduplicated():
    """Headings repeat legitimately; removing them destroys the structure."""
    seen: set[str] = set()
    dedupe("## Requirements", seen)
    body, removed = dedupe("## Requirements", seen)
    assert removed == 0
    assert body == "## Requirements"


def test_the_optimizer_deduplicates_across_items():
    fact = ("Constraint: the demo must run entirely offline, because the venue "
            "wifi is not reliable and a failed demo scores zero regardless of "
            "how good the code underneath it happens to be.")
    o = TokenOptimizer(".", cache=False)
    result = o.optimize(
        [
            ContextItem("problem", f"## Problem\n\n{fact}", P.TASK),
            ContextItem("upstream", f"## Upstream\n\n{fact}", P.STATE),
        ],
        Budget(context=4_000),
    )
    assert result.text.count("venue wifi is not reliable") == 1
    assert result.metrics.deduped_blocks >= 1


# -- prioritisation ---------------------------------------------------------


def test_priority_bands_follow_the_documented_order():
    assert P.TASK < P.DEPENDENCY < P.ARTIFACT < P.STATE < P.KNOWLEDGE < P.HISTORY < P.BACKGROUND


def test_a_required_input_is_promoted_to_a_dependency():
    assert key_priority("architecture") == P.ARTIFACT
    assert key_priority("architecture", ("PRODUCT/architecture.md",)) == P.DEPENDENCY


def _incompressible(tag: str, n: int = 80) -> str:
    """Content where every line must be kept, so only the budget can bite.

    Compression is aggressive enough that ordinary prose never reaches the
    drop path -- which is correct behaviour, and useless for testing it.
    """
    return "\n".join(
        f"- **{tag} decision {i}**: chose option {i} because it was cheaper."
        for i in range(n)
    )


def test_low_priority_context_is_dropped_before_high(tmp_path):
    o = TokenOptimizer(tmp_path, cache=False)
    result = o.optimize(
        [
            ContextItem("problem", "## Problem\n\n" + _incompressible("task"), P.TASK),
            ContextItem("prior_art", "## Prior art\n\n" + _incompressible("history"), P.HISTORY),
            ContextItem("knowledge", "## Knowledge\n\n" + _incompressible("knowledge"), P.KNOWLEDGE),
        ],
        Budget(context=400),
    )
    assert "problem" in result.kept
    dropped = {k for k, _why in result.dropped}
    assert {"prior_art", "knowledge"} & dropped, "low bands should go first"
    assert "problem" not in dropped


def test_protected_context_is_never_dropped(tmp_path):
    """A specialist that does not know its own task wastes a whole run."""
    o = TokenOptimizer(tmp_path, cache=False)
    big = "Low priority detail. " * 400
    result = o.optimize(
        [
            ContextItem("background", big, P.BACKGROUND),
            ContextItem("problem", "## Problem\n\nBuild the triage scorer.", P.TASK),
        ],
        Budget(context=120),
    )
    assert "problem" in result.kept
    assert "Build the triage scorer" in result.text


# -- compression ------------------------------------------------------------


LARGE_ARTIFACT = """# Architecture

## Overview

""" + ("This paragraph is ordinary connective prose that explains context. " * 40) + """

## Decisions

- Decided to use SQLite rather than Postgres, because the demo must run offline.
- Rejected a message queue: it adds a process the judge would have to start.

## Interfaces

- `POST /triage` accepts `{text: str}` and returns `{score: int, reason: str}`.
- `src/backend/api.py` owns routing; `src/ml/pipeline.py` owns scoring.

## Constraints

- Must run with no network access.
- Python 3.11 only.

""" + ("More filler prose that nobody downstream will ever need to read. " * 40) + """

## Acceptance criteria

- AC-1: a nurse can score a case in under two seconds.
- AC-2: every scoring call writes an audit row.

## Open questions

- TODO: nobody has decided what happens when the model is unavailable.
"""


def test_compression_keeps_decisions_interfaces_constraints_and_criteria():
    out, changed = compress(LARGE_ARTIFACT, 260)
    assert changed
    assert estimate_tokens(out) < estimate_tokens(LARGE_ARTIFACT)
    for needle in ("SQLite", "POST /triage", "no network access",
                   "AC-1", "AC-2", "TODO"):
        assert needle in out, f"compression lost {needle!r}"


def test_compression_drops_the_filler_it_was_meant_to_drop():
    out, _ = compress(LARGE_ARTIFACT, 260)
    assert "nobody downstream will ever need to read" not in out


def test_compression_says_the_text_is_abridged():
    """An agent that thinks it got the whole document will not go and read it."""
    out, _ = compress(LARGE_ARTIFACT, 200, label="PRODUCT/architecture.md")
    assert "compressed" in out
    assert "read_file" in out


def test_small_text_is_returned_untouched():
    out, changed = compress("# Short\n\nNothing to do here.", 5_000)
    assert not changed
    assert out == "# Short\n\nNothing to do here."


def test_excerpt_prefers_the_sections_matching_the_task(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text(
        "# Doc\n\n## Payments\n\nStripe integration details go here, at length. " * 1
        + "\n\n## Triage scoring\n\nThe urgency model and its thresholds. " * 1,
        encoding="utf-8",
    )
    out = excerpt_file(p, ("triage", "scoring"), 40)
    assert "urgency model" in out


# -- budgets ----------------------------------------------------------------


def test_budgets_scale_with_task_complexity():
    spec = roster.get("architect")
    cheap = Task(id="a", agent="architect", objective="x", impact=1, effort=1,
                 priority=Priority.LOW)
    dear = Task(id="b", agent="architect", objective="x", impact=5, effort=5,
                priority=Priority.CRITICAL)
    assert budget_for(spec, dear, project_complexity=5).context > \
        budget_for(spec, cheap, project_complexity=1).context


def test_a_specialist_with_no_research_tools_gets_no_research_budget():
    assert budget_for(roster.get("presentation_builder")).research == 0
    assert budget_for(roster.get("technical_researcher")).research > 0


def test_output_budget_never_exceeds_the_spec_ceiling():
    for name in roster.REGISTRY:
        spec = roster.get(name)
        assert budget_for(spec, project_complexity=5).output <= spec.max_tokens


def test_budget_enforcement_actually_bounds_what_is_sent(tmp_path):
    o = TokenOptimizer(tmp_path, cache=False)
    items = [
        ContextItem(f"slice{i}", "Long artifact body with detail. " * 200,
                    P.ARTIFACT, source=f"doc{i}.md")
        for i in range(6)
    ]
    budget = Budget(context=800)
    result = o.optimize(items, budget)
    # Allow the protected-band headroom the optimiser grants, but nothing near
    # the ~12k tokens a naive builder would have sent.
    assert result.tokens <= budget.context * 1.3
    assert result.metrics.context_tokens_removed > 5_000


def test_budgets_round_trip():
    b = Budget(context=1, output=2, research=3, complexity=4, reason="why")
    assert Budget.from_dict(b.to_dict()) == b


# -- cache / reuse ----------------------------------------------------------


def test_a_repeated_compression_is_served_from_cache(tmp_path):
    o = TokenOptimizer(tmp_path, cache=True)
    item = ContextItem("architecture", LARGE_ARTIFACT, P.ARTIFACT)
    o.optimize([item], Budget(context=300))
    hits_before = o.cache.hits
    o.optimize([ContextItem("architecture", LARGE_ARTIFACT, P.ARTIFACT)],
               Budget(context=300))
    assert o.cache.hits > hits_before


def test_the_cache_survives_a_reload(tmp_path):
    o = TokenOptimizer(tmp_path, cache=True)
    o.optimize([ContextItem("architecture", LARGE_ARTIFACT, P.ARTIFACT)],
               Budget(context=300))
    again = TokenOptimizer(tmp_path, cache=True)
    assert again.cache.entries


def test_editing_the_content_invalidates_its_cached_summary(tmp_path):
    """Content-addressed keys make this correct by construction."""
    a = SummaryCache.key("architecture", LARGE_ARTIFACT, 300)
    b = SummaryCache.key("architecture", LARGE_ARTIFACT + "\nnew decision", 300)
    assert a != b


def test_research_can_be_reused_rather_than_repeated(tmp_path):
    o = TokenOptimizer(tmp_path, cache=True)
    assert o.reuse("web", "triage market size") is None
    o.remember("web", "triage market size", "the answer")
    assert o.reuse("web", "Triage Market Size ") == "the answer"


def test_a_corrupt_cache_costs_a_recompute_not_a_crash(tmp_path):
    (tmp_path / "AGENT/cache").mkdir(parents=True)
    (tmp_path / "AGENT/cache/context_cache.json").write_text("{not json", encoding="utf-8")
    o = TokenOptimizer(tmp_path, cache=True)
    assert o.cache.entries == {}


def test_disabling_the_cache_disables_lookups(tmp_path):
    o = TokenOptimizer(tmp_path, cache=False)
    o.cache.put("k", "v")
    assert o.cache.get("k") is None


# -- metrics ----------------------------------------------------------------


def test_metrics_expose_every_name_the_status_command_reports():
    d = OptimizerMetrics().to_dict()
    for key in ("estimated_input_tokens", "estimated_output_tokens",
                "context_tokens_removed", "cache_hits", "cache_misses",
                "context_compression_ratio"):
        assert key in d


def test_compression_ratio_is_one_when_nothing_was_removed():
    assert OptimizerMetrics(estimated_input_tokens=500).context_compression_ratio == 1.0


def test_compression_ratio_falls_as_context_is_removed():
    m = OptimizerMetrics(estimated_input_tokens=400, context_tokens_removed=600)
    assert m.context_compression_ratio == 0.4


def test_metrics_round_trip():
    m = OptimizerMetrics(estimated_input_tokens=10, cache_hits=2, deduped_blocks=3)
    back = OptimizerMetrics.from_dict(m.to_dict())
    assert back.estimated_input_tokens == 10
    assert back.cache_hits == 2
    assert back.deduped_blocks == 3


# -- integration ------------------------------------------------------------


@pytest.fixture
def project(tmp_path) -> ProjectState:
    return ProjectState.create(
        tmp_path / "p", "Opt",
        problem="Build an AI triage assistant with a dashboard and an API.",
        judging="Impact and technical depth.",
        submission="A README and a deck.",
        constraints="Python only.",
    )


def test_optimisation_is_active_during_a_real_run(project):
    o = Orchestrator(project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.run(max_waves=25)
    m = project.token_metrics
    assert m.optimised_tasks == len(project.graph.tasks)
    assert m.estimated_input_tokens > 0
    assert m.context_tokens_removed > 0
    assert 0 < m.context_compression_ratio <= 1.0


def test_metrics_survive_a_reload(project):
    o = Orchestrator(project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.run(max_waves=25)
    back = ProjectState.load(project.root)
    assert back.token_metrics.estimated_input_tokens == project.token_metrics.estimated_input_tokens
    assert back.budgets


def test_the_optimizer_sends_less_than_the_candidate_context(project):
    """The whole point, measured rather than asserted.

    The baseline is the candidate context -- every slice this task is entitled
    to, at full size -- because that is what a system with no optimiser would
    have sent. Measured per task, and then in total across the run.
    """
    o = Orchestrator(project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.run(max_waves=25)

    total_raw = total_sent = 0
    for task in project.graph.tasks.values():
        raw = sum(item.tokens for item in o.candidate_context(task))
        sent, budget = o.build_context(task)
        assert estimate_tokens(sent) <= budget.context * 1.3, task.id
        total_raw += raw
        total_sent += estimate_tokens(sent)

    assert total_sent < total_raw
    assert project.token_metrics.context_tokens_removed > 0


def test_the_run_costs_less_context_than_a_flat_per_task_cap(project):
    """Budgets follow need, so the average must land below the old flat cap.

    The previous builder gave every specialist the same 9000-character ceiling
    whatever it was doing. Sizing by task is only an improvement if the total
    goes down while the tasks that genuinely need breadth get more.
    """
    from hackathon_os.context import BUDGET_TOTAL

    o = Orchestrator(project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.run(max_waves=25)

    flat_cap_tokens = estimate_tokens("x" * BUDGET_TOTAL) * len(project.graph.tasks)
    assert project.token_metrics.estimated_input_tokens < flat_cap_tokens


def test_a_task_still_receives_what_it_depends_on(project):
    """Cheaper is only better if the specialist can still do the work."""
    o = Orchestrator(project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.run(max_waves=25)
    task = project.graph.tasks["ux"]
    text, _b = o.build_context(task)
    assert "product_plan" in text or "PRODUCT/product_plan.md" in text
