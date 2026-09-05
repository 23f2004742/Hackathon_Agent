"""Orchestrator behaviour: parallelism, self-correction, and cost control.

The first two tests here are regressions for bugs the synthetic hackathon
found. Both were invisible in a green run and would have corrupted a red one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hackathon_os import agents as roster
from hackathon_os.agents.base import _Outcome
from hackathon_os.handoff import AgentResult, Priority, Status
from hackathon_os.llm import Backend, SimulatedBackend
from hackathon_os.orchestrator import Orchestrator
from hackathon_os.state import ProjectState
from hackathon_os.taskgraph import Task


@pytest.fixture
def project(tmp_path) -> ProjectState:
    return ProjectState.create(
        tmp_path / "p", "Test",
        problem="Build an ML model with a dashboard and an API.",
        judging="Impact and technical depth.",
        submission="A zip and a README.",
        constraints="Python only.",
    )


def orch(state: ProjectState, backend=None, **kw) -> Orchestrator:
    return Orchestrator(state, backend or SimulatedBackend(verbose=False),
                        verbose=False, **kw)


# -- regression: parallel result attribution --------------------------------


def test_parallel_results_are_paired_with_their_own_task(project):
    """Grouping reorders the wave; positional zipping filed results wrongly.

    With four research agents running concurrently, each result must land on
    the task that produced it -- otherwise a single failure marks the wrong
    agent failed and the wrong subtree unreachable.
    """
    o = orch(project, parallel=4)
    o.plan()
    ready = o.next_actions()
    assert len(ready) > 1, "need a genuinely parallel wave to test this"
    pairs = o.run_wave(ready)
    for task, result in pairs:
        assert result.task_id == task.id
        assert result.agent == task.agent
        for art in result.artifacts:
            owner = roster.get(task.agent)
            assert owner._in_scope(art.path), (
                f"{task.id} ({task.agent}) credited with {art.path}, "
                f"which is outside its write scope"
            )


def test_overlapping_write_scopes_never_run_concurrently(project):
    """Two agents appending to the same ledger concurrently lose writes."""
    o = orch(project, parallel=4)
    o.plan()
    ready = o.next_actions()
    scopes = {t.id: set(roster.get(t.agent).write_paths) for t in ready}
    shared = [t for t in ready if "RESEARCH/sources.json" in scopes[t.id]]
    assert len(shared) > 1, "expected several researchers sharing the ledger"
    # They are grouped, so they serialise; the run must still finish cleanly.
    pairs = o.run_wave(ready)
    assert all(r.status is Status.COMPLETED for _t, r in pairs)


# -- self-correction --------------------------------------------------------


class AlwaysFails(Backend):
    """A backend whose agents stop without producing anything."""

    name = "always-fails"

    def __init__(self):
        self.calls = 0

    def run(self, *, system, user, tools, spec, ctx):
        self.calls += 1
        return _Outcome(error="simulated model failure")


def test_a_failing_task_is_requeued_once_then_abandoned(project):
    backend = AlwaysFails()
    o = orch(project, backend)
    o.plan()
    task = o.state.graph.tasks["requirements"]

    o.step()
    assert task.attempts == 1
    assert task.status is None, "first failure should re-queue the task"
    assert "Previous attempt failed" in task.objective

    o.step()
    assert task.attempts == 2
    assert task.status is Status.FAILED, "second failure should give up"


def test_self_correction_records_its_reasoning(project):
    o = orch(project, AlwaysFails())
    o.plan()
    o.step()
    log = (project.root / "AGENT/decision_log.md").read_text(encoding="utf-8")
    assert "Retry requirements after failure" in log
    assert "downstream affected" in log
    assert any("failed" in n for n in o.state.notes)


def test_failure_makes_downstream_unreachable(project):
    o = orch(project, AlwaysFails())
    o.plan()
    for _ in range(3):
        o.step()
    assert o.state.graph.tasks["requirements"].status is Status.FAILED
    unreachable = {t.id for t in o.state.graph.blocked()}
    assert "product_plan" in unreachable
    assert "architecture" in unreachable
    assert o.state.blockers()


def test_next_best_action_is_always_actionable(project):
    o = orch(project)
    o.plan()
    assert "Run `requirements`" in o.next_best_action()
    o.run(max_waves=25)
    assert o.next_best_action()


# -- proposed follow-up work -----------------------------------------------


def test_agents_cannot_inject_tasks_directly(project):
    """next_tasks are proposals; only the Orchestrator schedules them."""
    o = orch(project)
    o.plan()
    before = set(o.state.graph.tasks)
    result = AgentResult(
        status=Status.COMPLETED, agent="tester",
        next_tasks=[{"agent": "developer", "objective": "fix login",
                     "priority": "CRITICAL", "reason": "500 on submit"}],
    )
    result = AgentResult.from_dict(result.to_dict())
    o._absorb_next_tasks(o.state.graph.tasks["test"], result)
    added = set(o.state.graph.tasks) - before
    assert added == {"test_fix1"}
    assert o.state.graph.tasks["test_fix1"].agent == "developer"
    assert o.state.graph.tasks["test_fix1"].depends_on == ("test",)


def test_low_priority_proposals_are_ignored(project):
    o = orch(project)
    o.plan()
    before = set(o.state.graph.tasks)
    result = AgentResult(
        status=Status.COMPLETED, agent="tester",
        next_tasks=[{"agent": "developer", "objective": "tidy imports",
                     "priority": "LOW", "reason": "style"}],
    )
    result = AgentResult.from_dict(result.to_dict())
    o._absorb_next_tasks(o.state.graph.tasks["test"], result)
    assert set(o.state.graph.tasks) == before


# -- cost control -----------------------------------------------------------


def test_cheap_roles_use_a_cheaper_model():
    assert roster.get("brand_designer").model == "claude-sonnet-5"
    assert roster.get("ml_engineer").effort == "xhigh"
    assert roster.get("final_auditor").effort == "xhigh"


def test_context_is_targeted_not_the_whole_workspace(project):
    from hackathon_os.context import ContextBuilder
    o = orch(project)
    o.plan()
    o.run(max_waves=25)
    spec = roster.get("brand_designer")
    built = ContextBuilder(project.root).build(spec.context_keys)
    assert len(built) < 12_000, "context budget blown"
    # A brand designer has no business receiving the test report.
    assert "test_report" not in built


def test_cost_is_tracked(project):
    o = orch(project)
    o.plan()
    o.run(max_waves=25)
    cost = o.state.cost()
    assert cost["agent_runs"] > 0
    assert cost["tool_calls"] > 0


# -- resume -----------------------------------------------------------------


class CountingBackend(SimulatedBackend):
    """A simulated backend that records how many tasks reached it."""

    def __init__(self):
        super().__init__(verbose=False)
        self.runs = 0

    def run(self, **kw):
        self.runs += 1
        return super().run(**kw)


def test_an_interrupted_run_resumes_without_redoing_a_single_task(project):
    """Interruption is the normal case on a subscription, not the edge case.

    The guarantee is counted rather than asserted: across the interrupted run
    and the resumed one, each task reaches the backend exactly once.
    """
    first = CountingBackend()
    o = orch(project, first)
    o.plan()
    for _ in range(3):            # stop partway, as a usage limit would
        o.step()
    done_at_stop = {t.id for t in project.graph.tasks.values() if t.done}
    assert done_at_stop and len(done_at_stop) < len(project.graph.tasks)

    reloaded = ProjectState.load(project.root)
    second = CountingBackend()
    orch(reloaded, second).run(max_waves=25)

    assert all(t.done for t in reloaded.graph.tasks.values())
    assert first.runs + second.runs == len(reloaded.graph.tasks), (
        "a task was executed twice across the interruption"
    )
    # Everything finished before the stop is still finished, untouched.
    for tid in done_at_stop:
        assert reloaded.graph.tasks[tid].done
        assert reloaded.graph.tasks[tid].attempts == 1


def test_re_running_an_unchanged_task_replays_from_the_ledger(project):
    """The ledger is what makes a forced replay free rather than expensive."""
    o = orch(project)
    o.plan()
    o.run(max_waves=25)

    reloaded = ProjectState.load(project.root)
    # A task whose context is fully settled once the run has finished.
    task = reloaded.graph.tasks["requirements"]
    task.status = None
    second = CountingBackend()
    o2 = orch(reloaded, second)
    result = o2.run_task(task)

    assert second.runs == 0, "an unchanged task reached the model again"
    assert o2.ledger.hits == 1
    assert any("ledger" in n for n in result.notes)


def test_every_planning_decision_survives_a_reload(project):
    o = orch(project)
    o.plan()
    o.run(max_waves=25)
    back = ProjectState.load(project.root)

    assert back.selection.chosen == project.selection.chosen
    assert back.selection.skipped
    assert back.selection.analysis.capabilities
    assert back.model_decisions.keys() == project.model_decisions.keys()
    assert back.budgets.keys() == project.budgets.keys()
    assert back.token_metrics.optimised_tasks == project.token_metrics.optimised_tasks
    assert back.phase == project.phase


def test_a_reloaded_project_reports_the_same_dashboard(project):
    from hackathon_os.dashboard import render

    o = orch(project)
    o.plan()
    o.run(max_waves=25)
    before = render(project)
    after = render(ProjectState.load(project.root))
    assert before == after


def test_the_dashboard_reports_every_new_section(project):
    from hackathon_os.dashboard import render

    o = orch(project)
    o.plan()
    o.run(max_waves=25)
    text = render(project)
    for heading in ("TASKS", "SPECIALISTS", "MODELS", "TOKEN OPTIMIZATION"):
        assert heading in text, f"status is missing the {heading} section"
    assert "Estimated input" in text
    assert "Cache hits" in text
    assert "Compression ratio" in text


def test_status_works_on_a_project_with_no_plan_yet(tmp_path):
    from hackathon_os.dashboard import render

    st = ProjectState.create(tmp_path / "fresh", "Fresh", problem="Build something.")
    assert "No plan yet" in render(st)
