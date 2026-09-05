"""Artifact contracts, the handoff protocol, and the task graph."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from hackathon_os import agents as roster
from hackathon_os.agents.base import AgentSpec, FileContains, HasHeadings, MinWords, ValidJson
from hackathon_os.handoff import AgentResult, Artifact, Finding, Priority, Severity, Status
from hackathon_os.orchestrator import build_plan, detect, select_specialists
from hackathon_os.taskgraph import CycleError, Task, TaskGraph
from hackathon_os.tools import REGISTRY, ExecutionContext, using


# -- artifact contracts -----------------------------------------------------


def test_completed_without_artifacts_is_downgraded(tmp_path):
    """An agent cannot report success it did not earn."""
    spec = roster.get("market_researcher")
    r = AgentResult(status=Status.COMPLETED, agent=spec.name, summary="all done!")
    r.validate_against(spec, tmp_path)
    assert r.status is Status.FAILED
    assert "artifact contract not met" in r.notes[-1]


def test_stub_artifact_is_rejected(tmp_path):
    spec = roster.get("market_researcher")
    p = tmp_path / "RESEARCH/market_report.md"
    p.parent.mkdir(parents=True)
    p.write_text("# done", encoding="utf-8")   # far under min_artifact_bytes
    r = AgentResult(status=Status.COMPLETED, agent=spec.name)
    r.validate_against(spec, tmp_path)
    assert r.status is Status.FAILED
    assert "under" in r.notes[-1]


def test_postconditions_catch_a_document_missing_its_topics(tmp_path):
    spec = roster.get("market_researcher")
    p = tmp_path / "RESEARCH/market_report.md"
    p.parent.mkdir(parents=True)
    # Long enough to pass the size floor, but says nothing it was asked to say.
    p.write_text("# Market\n\n" + ("Lorem ipsum dolor sit amet. " * 90), encoding="utf-8")
    r = AgentResult(status=Status.COMPLETED, agent=spec.name)
    r.validate_against(spec, tmp_path)
    assert r.status is Status.FAILED
    assert "does not cover" in r.notes[-1]


def test_missing_required_input_blocks_rather_than_fails(tmp_path):
    spec = roster.get("product_manager")
    assert spec.missing_inputs(tmp_path) == ["PRODUCT/requirements.md"]


def test_checks_pass_on_a_conforming_document(tmp_path):
    (tmp_path / "d.md").write_text(
        "# T\n## A\n## B\n## C\n" + ("word " * 200), encoding="utf-8"
    )
    assert HasHeadings("d.md", 3)(tmp_path) is None
    assert MinWords("d.md", 100)(tmp_path) is None
    assert FileContains("d.md", ("word",))(tmp_path) is None


def test_valid_json_check(tmp_path):
    (tmp_path / "m.json").write_text('{"deliverables": [], "verified": false}', encoding="utf-8")
    assert ValidJson("m.json", ("deliverables", "verified"))(tmp_path) is None
    assert "lacks keys" in ValidJson("m.json", ("missing",))(tmp_path)
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert "not valid JSON" in ValidJson("bad.json")(tmp_path)


# -- handoff protocol -------------------------------------------------------


def test_handoff_round_trips():
    r = AgentResult(
        status=Status.COMPLETED, agent="tester", summary="ran the suite",
        artifacts=[Artifact(path="VALIDATION/test_report.md", bytes=900)],
        findings=[Finding(summary="login 500s", severity=Severity.CRITICAL)],
        assumptions=["assumed sqlite"], risks=["demo needs seed data"],
    )
    back = AgentResult.from_dict(json.loads(r.to_json()))
    assert back.status is Status.COMPLETED
    assert back.findings[0].severity is Severity.CRITICAL
    assert back.artifacts[0].path == "VALIDATION/test_report.md"
    assert back.assumptions == ["assumed sqlite"]


def test_handoff_tool_rejects_malformed_json(tmp_path):
    ctx = ExecutionContext(root=tmp_path, agent="tester",
                           allowed_tools=frozenset({"submit_handoff"}))
    with using(ctx):
        out = REGISTRY["submit_handoff"].fn(
            status="completed", summary="x", findings_json="{not a list}"
        )
    assert "not valid JSON" in out
    assert ctx.handoff is None


def test_handoff_tool_rejects_unknown_status(tmp_path):
    ctx = ExecutionContext(root=tmp_path, agent="tester",
                           allowed_tools=frozenset({"submit_handoff"}))
    with using(ctx):
        out = REGISTRY["submit_handoff"].fn(status="probably_fine", summary="x")
    assert "status must be one of" in out


def test_handoff_tool_accepts_a_good_report(tmp_path):
    ctx = ExecutionContext(root=tmp_path, agent="tester",
                           allowed_tools=frozenset({"submit_handoff"}))
    with using(ctx):
        REGISTRY["submit_handoff"].fn(
            status="completed", summary="done",
            findings_json='[{"summary":"x","severity":"HIGH"}]',
        )
    assert ctx.handoff["status"] == "completed"
    assert ctx.handoff["findings"][0]["severity"] == "HIGH"


# -- task graph -------------------------------------------------------------


def test_cycle_is_rejected():
    g = TaskGraph()
    g.add(Task(id="a", agent="developer", objective="a"))
    g.add(Task(id="b", agent="developer", objective="b", depends_on=("a",)))
    g.tasks["a"].depends_on = ("b",)
    with pytest.raises(CycleError):
        g.validate()


def test_ready_respects_dependencies():
    g = TaskGraph()
    g.add(Task(id="a", agent="developer", objective="a"))
    g.add(Task(id="b", agent="developer", objective="b", depends_on=("a",)))
    assert [t.id for t in g.ready()] == ["a"]
    g.tasks["a"].status = Status.COMPLETED
    assert [t.id for t in g.ready()] == ["b"]


def test_failure_propagates_to_unreachable_tasks():
    g = TaskGraph()
    g.add(Task(id="a", agent="developer", objective="a"))
    g.add(Task(id="b", agent="developer", objective="b", depends_on=("a",)))
    g.add(Task(id="c", agent="developer", objective="c", depends_on=("b",)))
    g.tasks["a"].status = Status.FAILED
    assert {t.id for t in g.blocked()} == {"b", "c"}


def test_priority_orders_before_value_density():
    g = TaskGraph()
    g.add(Task(id="cheap", agent="developer", objective="x",
               priority=Priority.LOW, impact=5, effort=1))
    g.add(Task(id="vital", agent="developer", objective="y",
               priority=Priority.CRITICAL, impact=3, effort=3))
    assert [t.id for t in g.ready()] == ["vital", "cheap"]


def test_graph_survives_a_save_load_round_trip(tmp_path):
    g = TaskGraph()
    g.add(Task(id="a", agent="developer", objective="a", priority=Priority.HIGH))
    g.tasks["a"].status = Status.COMPLETED
    g.save(tmp_path / "g.json")
    back = TaskGraph.load(tmp_path / "g.json")
    assert back.tasks["a"].status is Status.COMPLETED
    assert back.tasks["a"].priority is Priority.HIGH


# -- selection --------------------------------------------------------------


def test_negation_is_respected():
    """'No backend, no database' must not staff a backend team."""
    sig = detect("Build a static site. No backend, no database, no accounts.")
    assert sig["backend"] is False
    assert sig["database"] is False


def test_positive_signal_still_detected():
    assert detect("Build a REST API with a Postgres database")["backend"] is True
    assert detect("Build a REST API with a Postgres database")["database"] is True


def test_ml_brief_does_not_staff_a_frontend():
    sel = select_specialists(
        "Tabular leaderboard task, predict RUL, scored by RMSE. "
        "No interface required. Python only."
    )
    assert "ml_engineer" in sel.chosen
    assert "frontend_engineer" not in sel.chosen
    assert "ux_designer" not in sel.chosen


def test_design_work_always_gets_someone_to_build_it():
    sel = select_specialists("A beautiful, usable dashboard for nurses.")
    if {"ux_designer", "ui_designer"} & sel.chosen:
        assert "frontend_engineer" in sel.chosen


def test_plan_rewires_around_dropped_specialists():
    """Dropping a specialist must not orphan its dependants."""
    sel = select_specialists("Static informational website. No backend, no database.")
    g = build_plan(sel)
    g.validate()
    assert "ml" not in g.tasks
    for t in g.tasks.values():
        for d in t.depends_on:
            assert d in g.tasks, f"{t.id} depends on missing {d}"


def test_plan_is_acyclic_for_every_shape_of_brief():
    for brief in (
        "Build an ML model for churn prediction.",
        "Build a static marketing site. No backend.",
        "Build an LLM agent with a dashboard and a Postgres database, deployed to cloud.",
        "Write a research report. No code.",
    ):
        build_plan(select_specialists(brief)).validate()
