"""Packaging and the GitHub path.

Two questions, and the second matters more than the first: does the package
contain what a judge needs, and is it impossible to publish a credential by
accident? The blocking behaviour is tested from both directions -- a planted
key must stop the package, and a clean tree must not be stopped by a
placeholder in `.env.example`.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from hackathon_os import github as gh
from hackathon_os import packaging as pkg
from hackathon_os.llm import SimulatedBackend
from hackathon_os.orchestrator import Orchestrator
from hackathon_os.state import ProjectState


@pytest.fixture
def finished(tmp_path) -> ProjectState:
    """A completed project, on the simulated backend."""
    st = ProjectState.create(
        tmp_path / "proj", "Packaged",
        problem=("Build an AI triage assistant with a dashboard, an API and an "
                 "auditable log. Judged on whether a judge can run it from the repo."),
        judging="Impact 40%, demo 30%, docs 30%.",
        submission="A GitHub repository and a README.",
        constraints="Python only.",
    )
    o = Orchestrator(st, SimulatedBackend(verbose=False), parallel=3, verbose=False)
    o.plan()
    o.run(max_waves=25)
    (st.root / "requirements.txt").write_text("python-pptx>=1.0.0\n", encoding="utf-8")
    return st


def git_ok() -> bool:
    try:
        gh._git_path()
    except gh.GitUnavailable:
        return False
    return True


needs_git = pytest.mark.skipif(not git_ok(), reason="git is not installed")


# -- what ships -------------------------------------------------------------


def test_the_package_carries_source_docs_and_demo(finished):
    plan = pkg.build_package(finished.root)
    dests = {d for _p, d in plan.include}
    assert any(d.startswith("source/") for d in dests)
    assert any(d.startswith("docs/") for d in dests)
    assert "README.md" in dests
    assert "requirements.txt" in dests


def test_the_package_is_actually_written_to_disk(finished):
    pkg.build_package(finished.root)
    out = finished.root / pkg.PACKAGE_DIR
    assert (out / "README.md").is_file()
    assert (out / "PACKAGE_MANIFEST.json").is_file()
    manifest = json.loads((out / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["secret_scan"] == "PASS"
    assert manifest["file_count"] == len(manifest["files"])


def test_packaging_is_reproducible(finished):
    first = {d for _p, d in pkg.build_package(finished.root).include}
    second = {d for _p, d in pkg.build_package(finished.root).include}
    assert first == second, "a package that contains its own last output"


def test_the_package_never_contains_itself(finished):
    pkg.build_package(finished.root)
    plan = pkg.plan_package(finished.root)
    assert not any(d.startswith("dist/") for _p, d in plan.include)


# -- what does not ship -----------------------------------------------------


def test_env_is_excluded_but_env_example_is_kept(finished):
    (finished.root / ".env.example").write_text(
        "ANTHROPIC_API_KEY=sk-ant-your-key-here\n", encoding="utf-8"
    )
    plan = pkg.plan_package(finished.root)
    dests = {d for _p, d in plan.include}
    assert ".env.example" in dests, "a judge needs to know what config to supply"
    assert ".env" not in dests


def test_git_and_node_modules_and_caches_are_excluded(finished):
    for rel, body in (
        (".git/config", "[core]\n"),
        ("node_modules/left-pad/index.js", "module.exports = 1;\n"),
        ("__pycache__/x.cpython-313.pyc", "junk"),
        (".venv/pyvenv.cfg", "home = /usr\n"),
        ("src/model.ckpt", "weights"),
        ("run.log", "line\n"),
    ):
        p = finished.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    plan = pkg.plan_package(finished.root)
    dests = {d for _p, d in plan.include}
    for junk in ("config", "index.js", "x.cpython-313.pyc", "pyvenv.cfg",
                 "model.ckpt", "run.log"):
        assert not any(junk in d for d in dests), f"{junk} would have shipped"


def test_the_agents_own_bookkeeping_is_not_a_deliverable(finished):
    plan = pkg.plan_package(finished.root)
    dests = {d for _p, d in plan.include}
    assert not any("state.json" in d for d in dests)
    assert not any("ledger.json" in d for d in dests)


def test_an_oversized_file_is_dropped_with_a_reason(finished):
    big = finished.root / "data" / "huge.csv"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_text("x" * 5000, encoding="utf-8")
    rules = pkg.PackageRules(max_file_bytes=1000)
    plan = pkg.plan_package(finished.root, rules)
    reasons = {r: w for r, w in plan.excluded}
    assert "data/huge.csv" in reasons
    assert "larger than" in reasons["data/huge.csv"]


def test_project_rules_can_force_a_file_in_or_out(finished):
    (finished.root / "data").mkdir(exist_ok=True)
    (finished.root / "data/seed.db").write_text("sqlite-ish", encoding="utf-8")
    default = {d for _p, d in pkg.plan_package(finished.root).include}
    assert not any("seed.db" in d for d in default)

    forced = pkg.plan_package(finished.root, pkg.PackageRules(include=("data/seed.db",)))
    assert any("seed.db" in d for _p, d in forced.include)

    dropped = pkg.plan_package(finished.root, pkg.PackageRules(exclude=("README.md",)))
    assert "README.md" not in {d for _p, d in dropped.include}


# -- secrets ----------------------------------------------------------------


def test_a_clean_project_passes_the_scan(finished):
    report = pkg.scan_secrets(finished.root)
    assert report.passed
    assert "No secrets detected" in report.render()
    assert report.scanned > 0


def test_a_planted_key_blocks_the_package(finished):
    (finished.root / "src/config.py").write_text(
        'KEY = "sk-ant-api03-RealLookingKeyMaterial1234567890abcdef"\n', encoding="utf-8"
    )
    with pytest.raises(pkg.PackageBlocked) as e:
        pkg.build_package(finished.root)
    assert "BLOCKED" in str(e.value)
    assert "Anthropic API key" in str(e.value)


def test_a_dotenv_file_blocks_the_package_even_though_it_would_not_ship(finished):
    """It is still sitting in the directory `git add -A` will run over."""
    (finished.root / ".env").write_text("API_KEY=sk-ant-real-looking-value\n",
                                        encoding="utf-8")
    plan = pkg.plan_package(finished.root)
    assert not plan.scan.passed
    assert ".env" in plan.scan.blocked_files
    with pytest.raises(pkg.PackageBlocked):
        pkg.build_package(finished.root)


def test_a_placeholder_is_reported_but_does_not_block(finished):
    (finished.root / ".env.example").write_text(
        "ANTHROPIC_API_KEY=sk-ant-your-key-here-placeholder\n", encoding="utf-8"
    )
    plan = pkg.plan_package(finished.root)
    assert plan.scan.passed
    assert plan.scan.placeholders


def test_force_is_recorded_rather_than_silent(finished):
    (finished.root / "src/config.py").write_text(
        'KEY = "sk-ant-api03-RealLookingKeyMaterial1234567890abcdef"\n', encoding="utf-8"
    )
    pkg.build_package(finished.root, force=True)
    status = pkg.read_status(finished.root)
    assert status["forced"] is True
    manifest = json.loads(
        (finished.root / pkg.PACKAGE_DIR / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert manifest["secret_scan"] == "OVERRIDDEN"


def test_the_scan_finds_every_shape_of_credential(tmp_path):
    (tmp_path / "leak.py").write_text(
        "\n".join([
            # AKIA + exactly 16, and deliberately not AWS's own documentation
            # key -- that one contains "EXAMPLE" and is correctly filed as a
            # placeholder rather than a leak.
            'aws = "AKIA4TQ7HZNLVMWK2PXR"',
            'gh = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"',
            'db = "postgres://user:hunter2@db.internal:5432/app"',
        ]),
        encoding="utf-8",
    )
    report = pkg.scan_secrets(tmp_path)
    labels = {h.label for h in report.live}
    assert "AWS access key id" in labels
    assert "GitHub token" in labels
    assert "Database URL with credentials" in labels


# -- status -----------------------------------------------------------------


def test_package_status_is_persisted_for_the_dashboard(finished):
    pkg.build_package(finished.root)
    status = pkg.read_status(finished.root)
    assert status["built"] is True
    assert status["secret_scan"] == "PASS"
    assert status["file_count"] > 0


def test_a_corrupt_status_file_is_survivable(finished):
    (finished.root / pkg.STATUS_FILE).write_text("{not json", encoding="utf-8")
    assert pkg.read_status(finished.root) == {}


# -- github -----------------------------------------------------------------


@needs_git
def test_init_creates_a_repository_and_a_gitignore(finished):
    report = gh.init(finished.root)
    assert report.created_repo
    assert (finished.root / ".gitignore").is_file()
    assert gh.is_repo(finished.root)
    text = (finished.root / ".gitignore").read_text(encoding="utf-8")
    for rule in (".env", "node_modules/", "__pycache__/", "dist/", "AGENT/state.json"):
        assert rule in text


@needs_git
def test_init_is_idempotent(finished):
    gh.init(finished.root)
    again = gh.init(finished.root)
    assert not again.created_repo


@needs_git
def test_init_repairs_a_gitignore_that_does_not_exclude_secrets(finished):
    (finished.root / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    report = gh.init(finished.root)
    assert report.wrote_gitignore
    assert ".env" in (finished.root / ".gitignore").read_text(encoding="utf-8")
    assert any("did not exclude secrets" in n for n in report.notes)


@needs_git
def test_init_writes_a_readme_only_when_there_is_none(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    report = gh.init(root)
    assert report.wrote_readme
    assert (root / "README.md").is_file()


@needs_git
def test_validation_reports_what_a_reviewer_would_notice(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    problems = " ".join(gh.validate_repo(root))
    assert "README" in problems
    assert "requirements.txt" in problems
    assert "src/" in problems


@needs_git
def test_prepare_reports_gits_own_view_of_the_commit(finished):
    gh.init(finished.root)
    pkg.build_package(finished.root)      # so there is a dist/ to be ignored
    report = gh.prepare(finished.root)
    assert report.tracked, "nothing would be committed"
    assert "README.md" in report.tracked
    # The things .gitignore excludes must appear as excluded, not as committed.
    assert not any(t.startswith("dist/") for t in report.tracked)
    assert not any(t.endswith("state.json") for t in report.tracked)
    assert any(i.startswith("dist/") for i in report.ignored)
    assert any(i.endswith("state.json") for i in report.ignored)


@needs_git
def test_prepare_renders_the_four_sections_the_operator_reads(finished):
    gh.init(finished.root)
    text = gh.prepare(finished.root).render()
    for heading in ("FILES TO COMMIT", "FILES EXCLUDED", "SECRET SCAN",
                    "PACKAGE SIZE"):
        assert heading in text


@needs_git
def test_prepare_is_not_ready_while_a_secret_is_on_disk(finished):
    gh.init(finished.root)
    (finished.root / ".env").write_text("API_KEY=sk-ant-real-value-here\n",
                                        encoding="utf-8")
    report = gh.prepare(finished.root)
    assert not report.ready
    assert any("secret scan" in p for p in report.problems)


@needs_git
def test_push_refuses_without_explicit_confirmation(finished):
    gh.init(finished.root)
    with pytest.raises(gh.PushRefused):
        gh.push(finished.root)


@needs_git
def test_push_refuses_when_the_scan_fails_even_if_confirmed(finished):
    gh.init(finished.root)
    (finished.root / "leak.py").write_text(
        'k = "sk-ant-api03-RealLookingKeyMaterial1234567890abcdef"\n', encoding="utf-8"
    )
    with pytest.raises(pkg.PackageBlocked):
        gh.push(finished.root, confirmed=True)


@needs_git
def test_a_confirmed_push_commits_locally_and_stops_without_a_remote(finished):
    """No remote must mean a clear message, never a half-done publish."""
    gh.init(finished.root)
    report = gh.push(finished.root, confirmed=True, message="Submission")
    assert report.committed
    assert not report.pushed
    assert "no 'origin' remote" in report.message


@needs_git
def test_the_commit_contains_no_secrets_and_no_junk(finished):
    gh.init(finished.root)
    (finished.root / ".env.example").write_text("KEY=your-key-here\n", encoding="utf-8")
    pkg.build_package(finished.root)
    gh.push(finished.root, confirmed=True, message="Submission")
    listed = subprocess.run(
        [gh._git_path(), "-C", str(finished.root), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "README.md" in listed
    assert ".env.example" in listed
    for forbidden in (".env", "AGENT/state.json", "AGENT/cache/ledger.json"):
        assert forbidden not in listed
    assert not any(f.startswith("dist/") for f in listed)


@needs_git
def test_github_summary_feeds_the_dashboard(finished):
    gh.init(finished.root)
    summary = gh.summary(finished.root)
    assert summary["initialised"] is True
    assert "gh_cli" in summary
