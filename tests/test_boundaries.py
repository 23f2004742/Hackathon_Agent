"""The boundaries are the product.

If these pass, specialists differ in what they can actually do, not just in
what they are told. If they fail, this is a prompt collection wearing a
multi-agent costume.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from hackathon_os import agents as roster
from hackathon_os.tools import REGISTRY, ExecutionContext, ToolDenied, using
from hackathon_os.tools.base import guard


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for d in ("RESEARCH", "PRODUCT", "src/backend", "AGENT", "SUBMISSION"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


def ctx_for(agent: str, root: Path) -> ExecutionContext:
    spec = roster.get(agent)
    return ExecutionContext(
        root=root, agent=agent, write_paths=spec.write_paths,
        allowed_tools=frozenset(spec.tools), auto_approve=True,
    )


# -- tool allowlisting ------------------------------------------------------


def test_researcher_cannot_call_shell(project):
    """A research specialist is never handed run_shell."""
    assert "run_shell" not in roster.get("market_researcher").tools
    with using(ctx_for("market_researcher", project)):
        with pytest.raises(ToolDenied, match="not permitted"):
            guard("run_shell")


def test_engineer_can_call_shell(project):
    assert "run_shell" in roster.get("backend_engineer").tools
    with using(ctx_for("backend_engineer", project)):
        assert guard("run_shell").agent == "backend_engineer"


def test_tools_cannot_run_without_a_context():
    with pytest.raises(ToolDenied, match="no active execution context"):
        guard("read_file")


# -- write scoping ----------------------------------------------------------


def test_researcher_cannot_write_source(project):
    """The boundary holds in code, not in the prompt."""
    with using(ctx_for("market_researcher", project)):
        out = REGISTRY["write_file"].fn(path="src/backend/api.py", content="x = 1")
    assert "may not write" in out
    assert not (project / "src/backend/api.py").exists()


def test_researcher_can_write_its_own_report(project):
    with using(ctx_for("market_researcher", project)):
        out = REGISTRY["write_file"].fn(
            path="RESEARCH/market_report.md", content="# Market\n" * 20
        )
    assert "created" in out
    assert (project / "RESEARCH/market_report.md").is_file()


def test_tester_cannot_patch_the_code_it_tests(project):
    """A tester that can edit src/ makes reports green instead of products work."""
    with using(ctx_for("tester", project)):
        out = REGISTRY["write_file"].fn(path="src/backend/api.py", content="pass")
    assert "may not write" in out


def test_path_escape_is_rejected(project):
    with using(ctx_for("backend_engineer", project)):
        out = REGISTRY["write_file"].fn(path="../escaped.txt", content="nope")
    assert "escapes the project root" in out
    assert not (project.parent / "escaped.txt").exists()


def test_every_specialist_can_write_what_it_declares():
    """A spec that promises an artifact outside its scope is unbuildable."""
    for spec in roster.ALL_SPECS:
        for rel in spec.produces:
            assert spec._in_scope(rel), f"{spec.name} cannot write its own {rel}"


def test_no_two_specialists_produce_the_same_artifact():
    owners: dict[str, str] = {}
    for spec in roster.ALL_SPECS:
        for rel in spec.produces:
            assert rel not in owners, f"{rel} claimed by {owners[rel]} and {spec.name}"
            owners[rel] = spec.name


def test_specialists_are_actually_distinct():
    """No two agents share a full boundary signature."""
    sigs = {}
    for s in roster.ALL_SPECS:
        sig = (frozenset(s.tools), frozenset(s.write_paths),
               frozenset(s.produces), frozenset(s.requires))
        assert sig not in sigs, f"{s.name} is a duplicate of {sigs[sig]}"
        sigs[sig] = s.name


# -- error handling ---------------------------------------------------------


def test_tools_return_errors_rather_than_raising(project):
    """A raised exception kills the agent loop; a string lets it recover."""
    with using(ctx_for("backend_engineer", project)):
        out = REGISTRY["read_file"].fn(path="does/not/exist.py")
    assert isinstance(out, str)
    assert "no such file" in out


def test_result_truncation_protects_the_context_window(project):
    big = project / "src/backend/big.txt"
    big.write_text("x" * 80_000, encoding="utf-8")
    with using(ctx_for("backend_engineer", project)):
        out = REGISTRY["read_file"].fn(path="src/backend/big.txt")
    assert len(out) < 40_000
    assert "truncated" in out


def test_destructive_shell_commands_are_refused(project):
    with using(ctx_for("backend_engineer", project)):
        out = REGISTRY["run_shell"].fn(command="rm -rf / --no-preserve-root")
    assert "refused" in out


def test_relative_root_is_normalised(tmp_path, monkeypatch):
    """A relative root must not reject every path in its own project."""
    (tmp_path / "RESEARCH").mkdir()
    (tmp_path / "RESEARCH/market_report.md").write_text("hi", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    ctx = ExecutionContext(root=Path("."), agent="market_researcher",
                           allowed_tools=frozenset({"read_file"}))
    assert ctx.root.is_absolute()
    with using(ctx):
        out = REGISTRY["read_file"].fn(path="RESEARCH/market_report.md")
    assert "escapes" not in out
    assert "hi" in out


def test_dry_run_suppresses_every_write(project):
    """--dry-run must plan without touching disk.

    Note the semantics: a dry-run task then legitimately fails its artifact
    contract, because nothing was produced. That is the honest outcome, not a
    bug -- a dry run shows what would happen, it does not claim it happened.
    """
    spec = roster.get("market_researcher")
    ctx = ExecutionContext(
        root=project, agent=spec.name, write_paths=spec.write_paths,
        allowed_tools=frozenset(spec.tools), auto_approve=True, dry_run=True,
    )
    with using(ctx):
        out = REGISTRY["write_file"].fn(path="RESEARCH/market_report.md", content="x" * 900)
    assert "[dry-run]" in out
    assert not (project / "RESEARCH/market_report.md").exists()
