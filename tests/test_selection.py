"""Intelligent specialist selection, across six shapes of hackathon.

The claim being tested is narrow and important: different problems produce
different teams, and the difference is defensible. A selector that quietly
staffs all 28 specialists for everything would pass a "does it run" test and
fail every one of these.

Each project below is a real shape a hackathon actually takes. For each, the
suite asserts both halves: who must be there, and who must not.
"""

from __future__ import annotations

import json

import pytest

from hackathon_os import agents as roster
from hackathon_os.llm import SimulatedBackend
from hackathon_os.orchestrator import Orchestrator, build_plan
from hackathon_os.planner import (
    IMPLIES, Selection, analyse, apply_plan, detect, mandatory_for,
    negated_capabilities, parse_plan, planner_prompt, select_by_rules,
    select_specialists,
)
from hackathon_os.state import ProjectState


# ---------------------------------------------------------------------------
# Six synthetic projects
# ---------------------------------------------------------------------------

AI_WEB_APP = """
Build an AI-powered triage assistant for emergency-department nurses. It uses an
LLM to score patient urgency from free-text symptoms, shows a live queue in a web
dashboard, exposes a REST API for the hospital's existing systems, and stores every
decision in a Postgres database for audit. It must be deployable with Docker so the
judges can run it. Judged on impact, technical depth and the live demo.
"""

ML_NOTEBOOK = """
Tabular leaderboard task: predict remaining useful life of turbofan engines from
sensor readings. Scored by RMSE on a held-out set. Submit a Jupyter notebook and a
predictions CSV. No interface required, no API, no database, no deployment.
Python only.
"""

IOT_HARDWARE = """
Build a soil-moisture monitoring system for smallholder farms. Raspberry Pi with
sensors reports readings over LoRa to a small gateway; the firmware must survive
losing power. Show the readings somewhere simple. No machine learning required.
"""

BLOCKCHAIN_PAYMENTS = """
Build a settlement layer for cross-border remittances using smart contracts on
Ethereum. Users initiate a payment, the contract escrows funds, and settlement is
on-chain and auditable. Handle the payment flow end to end and address the security
of the escrow. Judged on the market opportunity and technical rigour.
"""

SIMPLE_FRONTEND = """
Build a static informational website for a local climate charity. It should explain
what they do, look good on a phone, and be easy for a volunteer to update. No
backend, no database, no accounts, no deployment pipeline.
"""

DATA_ANALYSIS = """
Analyse three years of city bike-share trip data and produce a report on usage
patterns, with charts. Deliver a CLI tool that regenerates the analysis and a
written report. No web interface, no API, no database beyond the CSVs.
"""

PROJECTS = {
    "ai_web": AI_WEB_APP,
    "ml_notebook": ML_NOTEBOOK,
    "iot": IOT_HARDWARE,
    "blockchain": BLOCKCHAIN_PAYMENTS,
    "frontend": SIMPLE_FRONTEND,
    "data": DATA_ANALYSIS,
}


# ---------------------------------------------------------------------------
# Stage 1: deterministic analysis
# ---------------------------------------------------------------------------


def test_capability_detection_separates_the_six_projects():
    caps = {k: analyse(v).capabilities for k, v in PROJECTS.items()}
    assert caps["ai_web"]["ai"] and caps["ai_web"]["backend"] and caps["ai_web"]["database"]
    assert caps["ml_notebook"]["ml"] and not caps["ml_notebook"]["backend"]
    assert caps["iot"]["hardware"] and not caps["iot"]["ml"]
    assert caps["blockchain"]["blockchain"] and caps["blockchain"]["payments"]
    assert not caps["frontend"]["backend"] and not caps["frontend"]["database"]
    assert caps["data"]["data"] and not caps["data"]["backend"]


def test_negation_is_read_as_a_statement_not_an_absence():
    """"No database" is information; "database never mentioned" is not."""
    excluded = negated_capabilities(ML_NOTEBOOK)
    assert "backend" in excluded
    assert "database" in excluded
    assert "ml" not in excluded


def test_project_type_is_inferred():
    assert "ai" in analyse(AI_WEB_APP).project_type
    assert "ml" in analyse(ML_NOTEBOOK).project_type
    assert "hardware" in analyse(IOT_HARDWARE).project_type
    assert "blockchain" in analyse(BLOCKCHAIN_PAYMENTS).project_type
    assert "data" in analyse(DATA_ANALYSIS).project_type


def test_complexity_tracks_the_breadth_of_the_build():
    assert analyse(AI_WEB_APP).complexity > analyse(SIMPLE_FRONTEND).complexity


def test_a_capability_with_no_specialist_is_reported_as_a_gap():
    """Silently pretending a project has no hardware in it helps nobody."""
    gaps = analyse(IOT_HARDWARE).gaps
    assert "hardware" in gaps
    assert "hardware" in gaps["hardware"] or "firmware" in gaps["hardware"]


# ---------------------------------------------------------------------------
# Selection per project shape
# ---------------------------------------------------------------------------


def test_ai_web_app_gets_the_full_engineering_team():
    sel = select_by_rules(AI_WEB_APP)
    for name in ("requirements_analyst", "architect", "ai_engineer",
                 "backend_engineer", "frontend_engineer", "database_engineer",
                 "devops_engineer", "ux_designer", "tester", "security_reviewer",
                 "technical_writer", "demo_engineer", "final_auditor",
                 "submission_manager"):
        assert name in sel.chosen, f"{name} missing from an AI web app"


def test_a_pure_ml_notebook_does_not_get_a_web_team():
    """The expensive mistake: five specialists building a UI nobody asked for."""
    sel = select_by_rules(ML_NOTEBOOK)
    assert "ml_engineer" in sel.chosen
    for name in ("frontend_engineer", "ui_designer", "ux_designer",
                 "backend_engineer", "database_engineer", "devops_engineer"):
        assert name not in sel.chosen, f"{name} staffed on a notebook submission"
        assert sel.skipped[name], f"{name} skipped with no reason recorded"


def test_a_static_site_gets_designers_but_no_backend():
    sel = select_by_rules(SIMPLE_FRONTEND)
    assert "frontend_engineer" in sel.chosen
    assert {"ux_designer", "ui_designer"} & sel.chosen
    assert "backend_engineer" not in sel.chosen
    assert "database_engineer" not in sel.chosen


def test_a_data_analysis_project_gets_no_interface_team():
    sel = select_by_rules(DATA_ANALYSIS)
    assert "backend_engineer" not in sel.chosen
    assert "database_engineer" not in sel.chosen
    assert "frontend_engineer" not in sel.chosen


def test_a_blockchain_payment_project_always_gets_security():
    sel = select_by_rules(BLOCKCHAIN_PAYMENTS)
    assert "security_reviewer" in sel.chosen
    assert "backend_engineer" in sel.chosen
    assert "market_researcher" in sel.chosen  # "market opportunity" is judged


def test_an_iot_project_reports_the_staffing_gap_it_cannot_fill():
    sel = select_by_rules(IOT_HARDWARE)
    assert sel.analysis.gaps.get("hardware")
    assert "ml_engineer" not in sel.chosen   # "No machine learning required"


def test_no_project_gets_every_specialist():
    for name, brief in PROJECTS.items():
        sel = select_by_rules(brief)
        assert len(sel.chosen) < len(roster.REGISTRY), f"{name} staffed everyone"


def test_the_six_projects_produce_distinct_teams():
    """Five distinct teams from six briefs, and the collision is a known gap.

    IoT and data analysis collapse onto the same staffing because the roster
    has a specialist for neither: both reduce to the core team plus the
    generalist developer. That is recorded as a gap rather than papered over
    with a specialist who would have nothing specific to do.
    """
    teams = {k: frozenset(select_by_rules(v).chosen) for k, v in PROJECTS.items()}
    assert len(set(teams.values())) >= 5, (
        "project shapes produced identical teams: "
        + str({k: sorted(v) for k, v in teams.items()})
    )
    assert teams["ai_web"] != teams["ml_notebook"] != teams["frontend"]
    assert teams["blockchain"] != teams["frontend"]


def test_the_roster_gap_is_declared_rather_than_hidden():
    for brief, cap in ((IOT_HARDWARE, "hardware"), (DATA_ANALYSIS, "data")):
        sel = select_by_rules(brief)
        assert cap in sel.analysis.gaps, f"{cap} gap not reported"
        assert "roster" in sel.analysis.gaps[cap]


# ---------------------------------------------------------------------------
# Explaining the decision
# ---------------------------------------------------------------------------


def test_every_selected_specialist_has_a_reason():
    for brief in PROJECTS.values():
        sel = select_by_rules(brief)
        for name in sel.chosen:
            assert sel.reasons.get(name), f"{name} selected with no reason"


def test_every_skipped_specialist_has_a_specific_reason():
    """"Not required" tells nobody anything they can disagree with."""
    sel = select_by_rules(ML_NOTEBOOK)
    for name, why in sel.skipped.items():
        if name in sel.chosen:
            continue
        assert why, f"{name} skipped silently"
        assert why.strip().lower() != "not required by this brief", (
            f"{name} skipped with the old generic reason"
        )


def test_a_skip_names_the_capability_that_would_have_selected_it():
    sel = select_by_rules(ML_NOTEBOOK)
    assert "frontend" in sel.skipped["frontend_engineer"]
    assert "database" in sel.skipped["database_engineer"]


def test_the_selection_persists_and_reloads(tmp_path):
    st = ProjectState.create(tmp_path / "p", "S", problem=AI_WEB_APP)
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    sel = o.plan()
    back = ProjectState.load(st.root)
    assert back.selection.chosen == sel.chosen
    assert back.selection.skipped
    assert back.selection.analysis.project_type == sel.analysis.project_type


def test_the_plan_document_records_both_halves(tmp_path):
    st = ProjectState.create(tmp_path / "p", "S", problem=ML_NOTEBOOK)
    Orchestrator(st, SimulatedBackend(verbose=False), verbose=False).plan()
    doc = (st.root / "AGENT/plan.md").read_text(encoding="utf-8")
    assert "Deliberately not activated" in doc
    assert "frontend_engineer" in doc


# ---------------------------------------------------------------------------
# Stage 2: the Claude capability planner
# ---------------------------------------------------------------------------


def test_the_planner_prompt_carries_the_brief_and_the_roster_only():
    prompt = planner_prompt(AI_WEB_APP)
    assert "triage assistant" in prompt
    assert "requirements_analyst" in prompt
    # Not the project, not the repository, not previous agent output.
    assert "state.json" not in prompt
    assert len(prompt) < 12_000


def test_a_fenced_json_reply_is_parsed():
    raw = 'Sure!\n```json\n{"project_type": ["ai"], "required_specialists": []}\n```\n'
    assert parse_plan(raw)["project_type"] == ["ai"]


def test_an_unparseable_reply_is_none_not_a_crash():
    assert parse_plan("I would rather not") is None
    assert parse_plan("") is None
    assert parse_plan("{ definitely not json") is None


def test_a_backend_without_a_planner_falls_back_to_rules():
    class Mute:
        pass

    sel = select_specialists(AI_WEB_APP, backend=Mute())
    assert not sel.planner_used
    assert "architect" in sel.chosen


def test_a_planner_that_raises_never_takes_the_run_down():
    class Broken:
        def ask_json(self, **kw):
            raise RuntimeError("model unavailable")

    sel = select_specialists(AI_WEB_APP, backend=Broken())
    assert not sel.planner_used
    assert sel.chosen


def test_the_planner_can_add_a_specialist_the_regexes_missed():
    sel = select_by_rules(ML_NOTEBOOK)
    assert "frontend_engineer" not in sel.chosen
    sel = apply_plan(sel, {
        "required_specialists": [{
            "agent": "frontend_engineer",
            "reason": "the submission notes reviewers open results in a browser",
            "priority": "medium",
        }],
    })
    assert "frontend_engineer" in sel.chosen
    assert "reviewers open results" in sel.reasons["frontend_engineer"]
    assert sel.choices["frontend_engineer"].source == "planner"


def test_the_planner_can_remove_a_specialist_the_regexes_guessed_at():
    """"Judged on the live demo" makes the regex staff a brand designer.

    That is the regex reasoning from a keyword rather than from the work, and
    it is exactly the kind of guess the planner exists to overrule.
    """
    sel = select_by_rules(AI_WEB_APP)
    assert "brand_designer" in sel.chosen
    sel = apply_plan(sel, {
        "excluded_specialists": [{
            "agent": "brand_designer",
            "reason": "the hospital brand is fixed; naming work would be wasted",
        }],
    })
    assert "brand_designer" not in sel.chosen
    assert "naming work would be wasted" in sel.skipped["brand_designer"]


def test_the_planner_may_not_remove_the_mandatory_spine():
    sel = select_by_rules(AI_WEB_APP)
    protected = mandatory_for(sel.analysis.project_type)
    sel = apply_plan(sel, {
        "excluded_specialists": [
            {"agent": n, "reason": "seems unnecessary"} for n in protected
        ],
    })
    for name in protected:
        assert name in sel.chosen, f"planner removed mandatory {name}"
        assert "mandatory" in sel.reasons[name]


def test_the_planner_may_not_overrule_a_capability_the_brief_states():
    """The brief says Postgres. That is not the planner's to reinterpret."""
    sel = select_by_rules(AI_WEB_APP)
    assert "database_engineer" in sel.chosen
    sel = apply_plan(sel, {
        "excluded_specialists": [
            {"agent": "database_engineer", "reason": "sqlite is probably fine"},
        ],
    })
    assert "database_engineer" in sel.chosen
    assert "states" in sel.reasons["database_engineer"]


def test_an_invented_specialist_is_dropped_not_created():
    sel = select_by_rules(SIMPLE_FRONTEND)
    before = set(sel.chosen)
    sel = apply_plan(sel, {
        "required_specialists": [
            {"agent": "quantum_engineer", "reason": "sounds impressive"},
        ],
    })
    assert sel.chosen == before
    assert "quantum_engineer" not in roster.REGISTRY


def test_a_planner_team_that_cannot_ship_is_repaired():
    """A UX spec with nobody to build it is a plan for an unfinished project."""
    sel = Selection()
    sel.analysis = analyse(SIMPLE_FRONTEND)
    sel.pick("ux_designer", "planner asked for it")
    sel = apply_plan(sel, {})
    assert "frontend_engineer" in sel.chosen
    assert sel.choices["frontend_engineer"].source == "coherence"


def test_the_simulated_planner_produces_a_valid_plan():
    raw = SimulatedBackend(verbose=False).ask_json(
        system="", user=planner_prompt(AI_WEB_APP)
    )
    plan = parse_plan(raw)
    assert plan is not None
    names = {r["agent"] for r in plan["required_specialists"]}
    assert names <= set(roster.REGISTRY)
    assert "architect" in names
    assert plan["excluded_specialists"]


def test_the_two_stage_path_runs_end_to_end():
    sel = select_specialists(AI_WEB_APP, backend=SimulatedBackend(verbose=False))
    assert sel.planner_used
    assert "architect" in sel.chosen
    assert len(sel.chosen) < len(roster.REGISTRY)


# ---------------------------------------------------------------------------
# The graph that comes out of it
# ---------------------------------------------------------------------------


def test_every_project_shape_produces_a_valid_acyclic_graph():
    for name, brief in PROJECTS.items():
        g = build_plan(select_by_rules(brief))
        g.validate()
        for t in g.tasks.values():
            for d in t.depends_on:
                assert d in g.tasks, f"{name}: {t.id} depends on missing {d}"


def test_a_smaller_team_produces_a_smaller_graph():
    big = build_plan(select_by_rules(AI_WEB_APP))
    small = build_plan(select_by_rules(ML_NOTEBOOK))
    assert len(small.tasks) < len(big.tasks)


# ---------------------------------------------------------------------------
# Replanning
# ---------------------------------------------------------------------------


@pytest.fixture
def notebook_project(tmp_path) -> ProjectState:
    return ProjectState.create(tmp_path / "nb", "Notebook", problem=ML_NOTEBOOK)


def test_replan_adds_a_specialist_without_restarting(notebook_project):
    o = Orchestrator(notebook_project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.run(max_waves=25)
    done_before = {t.id for t in notebook_project.graph.tasks.values() if t.done}
    assert "database" not in notebook_project.graph.tasks

    added = o.replan(["database"], because="ai_engineer (ai)")
    assert "database" in added
    assert "database_engineer" in notebook_project.selection.chosen
    # Nothing that was finished has been undone.
    still_done = {t.id for t in notebook_project.graph.tasks.values() if t.done}
    assert done_before <= still_done


def test_a_replan_task_gets_a_model_and_a_budget(notebook_project):
    o = Orchestrator(notebook_project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.replan(["database"], because="test")
    assert "database" in notebook_project.model_decisions
    assert "database" in notebook_project.budgets


def test_a_replan_is_recorded_for_a_human_to_question(notebook_project):
    o = Orchestrator(notebook_project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.replan(["database"], because="ai_engineer discovered persistence is needed")
    assert notebook_project.replans
    record = notebook_project.replans[-1]
    assert record["capabilities"] == ["database"]
    assert "persistence" in record["because"]
    log = (notebook_project.root / "AGENT/decision_log.md").read_text(encoding="utf-8")
    assert "Replanned" in log


def test_a_replan_never_overrules_what_the_brief_ruled_out(notebook_project):
    """The brief says "no database". An agent's opinion does not outrank it."""
    from hackathon_os.handoff import AgentResult, Status

    o = Orchestrator(notebook_project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    result = AgentResult(
        status=Status.COMPLETED, agent="ml_engineer",
        summary="This needs a database to hold intermediate features.",
    )
    added = o.consider_replan(notebook_project.graph.tasks["ml"], result)
    assert added == []
    assert "database_engineer" not in notebook_project.selection.chosen


def test_a_discovery_the_brief_did_not_rule_out_does_replan(tmp_path):
    from hackathon_os.handoff import AgentResult, Status

    st = ProjectState.create(
        tmp_path / "p", "P",
        problem="Build a web dashboard that summarises support tickets for a team lead.",
    )
    o = Orchestrator(st, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    assert "ml_engineer" not in st.selection.chosen
    result = AgentResult(
        status=Status.COMPLETED, agent="ai_engineer",
        summary="Prompting is not accurate enough; this needs a custom model trained "
                "on the ticket history.",
    )
    added = o.consider_replan(st.graph.tasks["architecture"], result)
    assert "ml" in added
    assert "ml_engineer" in st.selection.chosen
    st.graph.validate()


def test_a_replan_leaves_the_graph_acyclic(notebook_project):
    o = Orchestrator(notebook_project, SimulatedBackend(verbose=False), verbose=False)
    o.plan()
    o.replan(["database", "frontend", "devops"], because="test")
    notebook_project.graph.validate()
    for t in notebook_project.graph.tasks.values():
        for d in t.depends_on:
            assert d in notebook_project.graph.tasks
