"""Dynamic model selection.

The property under test is a bias, not a rule: the default model is chosen
unless something specific justifies otherwise. A planner that upgrades
everything is as wrong as one that never upgrades -- the first burns the plan's
separate weekly Opus window on slide assembly, the second sends the
architecture decision to a model that will get it wrong.
"""

from __future__ import annotations

import json

import pytest

from hackathon_os import agents as roster
from hackathon_os import model_planner as mp
from hackathon_os.handoff import Priority, Status
from hackathon_os.llm import SimulatedBackend
from hackathon_os.orchestrator import Orchestrator
from hackathon_os.routing import TIERS
from hackathon_os.state import ProjectState
from hackathon_os.taskgraph import Task


def task(tid: str, agent: str, **kw) -> Task:
    kw.setdefault("objective", "do the thing")
    return Task(id=tid, agent=agent, **kw)


# -- the catalogue ----------------------------------------------------------


def test_the_default_alias_resolves_to_a_real_model():
    assert mp.resolve("default") == mp.catalogue()[mp.default_alias()]
    assert mp.resolve("default").startswith("claude-")


def test_every_alias_resolves():
    for alias in mp.LADDER:
        assert mp.resolve(alias)


def test_a_concrete_model_id_resolves_to_itself():
    assert mp.resolve("claude-opus-5") == "claude-opus-5"


def test_an_invalid_model_name_is_rejected_loudly():
    """A typo that fell through to the default would waste a whole run."""
    for bad in ("gpt-4", "opuss", "claude-opus-9", "", "  "):
        with pytest.raises(mp.UnknownModel):
            mp.resolve(bad)
    assert not mp.is_valid("gpt-4")
    assert mp.is_valid("opus")


def test_the_default_is_configurable(monkeypatch):
    monkeypatch.setenv(mp.DEFAULT_MODEL_ENV, "haiku")
    assert mp.default_alias() == "haiku"
    assert mp.resolve("default") == mp.MODELS["haiku"]


def test_an_unknown_configured_default_falls_back_rather_than_breaking(monkeypatch):
    monkeypatch.setenv(mp.DEFAULT_MODEL_ENV, "not-a-model")
    assert mp.default_alias() == mp.BASE_DEFAULT


def test_the_catalogue_can_be_replaced_from_a_file(monkeypatch, tmp_path):
    """Model ids go stale; the table must not need a code change."""
    f = tmp_path / "models.json"
    f.write_text(json.dumps({"sonnet": "claude-sonnet-9"}), encoding="utf-8")
    monkeypatch.setenv(mp.MODELS_FILE_ENV, str(f))
    assert mp.resolve("sonnet") == "claude-sonnet-9"


def test_a_broken_models_file_is_ignored_not_fatal(monkeypatch, tmp_path):
    f = tmp_path / "models.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(mp.MODELS_FILE_ENV, str(f))
    assert mp.resolve("sonnet") == mp.MODELS["sonnet"]


def test_the_ladder_only_goes_up_and_stops_at_the_top():
    assert mp.stronger("haiku") == "sonnet"
    assert mp.stronger("sonnet") == "opus"
    assert mp.stronger("opus") == "opus"
    assert mp.rank("haiku") < mp.rank("sonnet") < mp.rank("opus")


# -- the policy -------------------------------------------------------------


def test_a_simple_task_stays_on_the_default():
    planner = mp.ModelPlanner(project_complexity=2)
    d = planner.decide(
        task("docs", "technical_writer", priority=Priority.HIGH, impact=4, effort=2),
        roster.get("technical_writer"),
    )
    assert d.model == mp.default_alias()
    assert "sufficient" in d.reason


def test_mechanical_work_drops_below_the_default():
    """There is no judgement to buy when turning markdown into slides."""
    planner = mp.ModelPlanner(project_complexity=3)
    d = planner.decide(
        task("slides", "presentation_builder", priority=Priority.HIGH, impact=4, effort=2),
        roster.get("presentation_builder"),
    )
    assert mp.rank(d.model) < mp.rank(mp.default_alias())


def test_hard_reasoning_work_can_upgrade():
    planner = mp.ModelPlanner(project_complexity=4)
    d = planner.decide(
        task("architecture", "architect", priority=Priority.CRITICAL, impact=5, effort=4),
        roster.get("architect"),
    )
    assert mp.rank(d.model) > mp.rank(mp.default_alias())
    assert "justifies an upgrade" in d.reason


def test_a_high_priority_task_is_not_automatically_upgraded():
    """"Do not blindly use Opus for every high-priority task."""
    planner = mp.ModelPlanner(project_complexity=2)
    d = planner.decide(
        task("submission", "submission_manager", priority=Priority.CRITICAL,
             impact=5, effort=2),
        roster.get("submission_manager"),
    )
    assert mp.rank(d.model) <= mp.rank(mp.default_alias())


def test_the_default_is_the_majority_answer_across_a_whole_project(tmp_path):
    st = ProjectState.create(
        tmp_path / "p", "P",
        problem="Build an AI assistant with a dashboard, an API and a database.",
    )
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    counts = o.models.summary()
    total = sum(counts.values())
    assert counts.get(mp.default_alias(), 0) > total / 2, counts
    assert counts.get("opus", 0) < total / 3, counts


def test_every_decision_carries_a_reason_and_a_confidence(tmp_path):
    st = ProjectState.create(tmp_path / "p", "P",
                             problem="Build an ML model and a dashboard.")
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    for d in o.models.decisions.values():
        assert d.reason
        assert 0.0 < d.confidence <= 1.0
        assert d.model_id.startswith("claude-")
        assert d.agent in roster.REGISTRY


def test_domain_risk_pushes_the_relevant_roles_up_only():
    caps = {"payments": True, "security": True}
    planner = mp.ModelPlanner(project_complexity=3)
    risky = planner.decide(
        task("security", "security_reviewer", priority=Priority.HIGH, impact=4, effort=2),
        roster.get("security_reviewer"), capabilities=caps,
    )
    unaffected = planner.decide(
        task("ux", "ux_designer", priority=Priority.MEDIUM, impact=3, effort=2),
        roster.get("ux_designer"), capabilities=caps,
    )
    assert mp.rank(risky.model) > mp.rank(unaffected.model)


# -- overrides --------------------------------------------------------------


def test_an_explicit_override_wins_everywhere():
    planner = mp.ModelPlanner(override="opus")
    for name in ("submission_manager", "architect", "brand_designer"):
        d = planner.decide(task(name, name), roster.get(name))
        assert d.model == "opus"
        assert d.forced
        assert d.confidence == 1.0


def test_an_invalid_override_is_rejected_before_any_work_happens():
    with pytest.raises(mp.UnknownModel):
        mp.ModelPlanner(override="gpt-4")


def test_a_forced_tier_pins_the_whole_run(monkeypatch):
    monkeypatch.setenv("HACKATHON_TIER", "light")
    planner = mp.ModelPlanner()
    d = planner.decide(task("architecture", "architect"), roster.get("architect"))
    assert d.model_id == TIERS["light"].model
    assert d.forced


def test_the_cli_override_reaches_the_task_graph(tmp_path):
    st = ProjectState.create(tmp_path / "p", "P", problem="Build an AI dashboard.")
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False, model="opus")
    o.plan()
    assert set(o.models.summary()) == {"opus"}


# -- escalation -------------------------------------------------------------


def test_a_failure_escalates_once_and_records_why():
    planner = mp.ModelPlanner(project_complexity=1)
    t = task("docs", "technical_writer")
    # `escalate` mutates the stored decision, so snapshot the value not the object.
    before = planner.decide(t, roster.get("technical_writer")).model
    after = planner.escalate("docs", "produced a stub")
    assert mp.rank(after.model) > mp.rank(before)
    assert after.escalations[-1]["from"] == before
    assert after.escalations[-1]["reason"] == "produced a stub"
    assert after.escalations[-1]["to"] == after.model


def test_escalation_stops_at_the_top_of_the_ladder():
    planner = mp.ModelPlanner(override="opus")
    planner.decide(task("a", "architect"), roster.get("architect"))
    # A forced decision is the operator's, not ours to escalate.
    assert planner.escalate("a", "failed").model == "opus"


def test_an_unknown_task_escalates_to_nothing_rather_than_crashing():
    assert mp.ModelPlanner().escalate("no-such-task", "why") is None


def test_a_real_failure_escalates_the_model_in_a_run(tmp_path):
    from hackathon_os.agents.base import _Outcome
    from hackathon_os.llm import Backend

    class AlwaysFails(Backend):
        name = "always-fails"

        def run(self, *, system, user, tools, spec, ctx):
            return _Outcome(error="simulated failure")

    st = ProjectState.create(tmp_path / "p", "P", problem="Build an AI dashboard.")
    o = Orchestrator(st, AlwaysFails(), verbose=False)
    o.plan()
    before = o.models.decisions["requirements"].model
    o.step()
    after = o.models.decisions["requirements"]
    assert mp.rank(after.model) > mp.rank(before)
    assert after.escalations
    assert any("model escalation" in n for n in st.notes)


def test_a_model_does_not_change_between_waves_without_a_failure(tmp_path):
    st = ProjectState.create(tmp_path / "p", "P", problem="Build an AI dashboard.")
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    before = {tid: d.model for tid, d in o.models.decisions.items()}
    o.run(max_waves=25)
    after = {tid: d.model for tid, d in o.models.decisions.items()}
    for tid, model in before.items():
        assert after[tid] == model, f"{tid} changed model with no failure"


# -- persistence ------------------------------------------------------------


def test_decisions_round_trip():
    d = mp.ModelDecision(task="a", agent="architect", model="opus",
                         model_id="claude-opus-5", reason="hard", confidence=0.9)
    back = mp.ModelDecision.from_dict(d.to_dict())
    assert back.model == "opus"
    assert back.reason == "hard"


def test_decisions_persist_across_a_reload(tmp_path):
    st = ProjectState.create(tmp_path / "p", "P",
                             problem="Build an AI assistant with a dashboard.")
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    expected = {tid: d.model for tid, d in o.models.decisions.items()}

    back = ProjectState.load(st.root)
    assert back.model_decisions
    o2 = Orchestrator(back, SimulatedBackend(verbose=False), verbose=False)
    assert {tid: d.model for tid, d in o2.models.decisions.items()} == expected


def test_a_resumed_run_does_not_re_decide_models(tmp_path):
    """Re-deciding would change the ledger fingerprint and re-run everything."""
    st = ProjectState.create(tmp_path / "p", "P", problem="Build an AI dashboard.")
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.models.escalate("requirements", "pretend this failed once")
    escalated = o.models.decisions["requirements"].model
    st.model_decisions = o.models.to_dict()
    st.save()

    o2 = Orchestrator(ProjectState.load(st.root), SimulatedBackend(verbose=False),
                      verbose=False)
    d = o2.models.decide(o2.state.graph.tasks["requirements"],
                         roster.get("requirements_analyst"))
    assert d.model == escalated


def test_the_effective_tier_keeps_routing_effort_and_takes_the_planned_model(tmp_path):
    """routing still owns *how hard*; the model planner owns *which model*."""
    from hackathon_os.routing import tier_name

    st = ProjectState.create(tmp_path / "p", "P", problem="Build an AI dashboard.")
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    t = st.graph.tasks["slides"]
    tier, decision = o.models.tier_for(t, roster.get("presentation_builder"))
    base = TIERS[tier_name("presentation_builder")]
    assert tier.effort == base.effort
    assert tier.max_turns == base.max_turns
    assert tier.model == decision.model_id
