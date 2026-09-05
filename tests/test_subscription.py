"""The subscription path: no API key, no paid fallback, boundaries intact.

These tests are the guardrail on the promise the README makes. The expensive
failure they exist to prevent is silent: a refactor that lets a paid credential
back in, or that hands a specialist Claude Code's built-in Write tool and so
dissolves the write scoping the whole design rests on. Neither shows up as a
crash -- the run just works, and bills, or lets the Tester patch `src/`.

Nothing here makes a model request.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hackathon_os import auth, routing
from hackathon_os.agents import REGISTRY, get, specialist
from hackathon_os.agents.base import _Outcome
from hackathon_os.auth import NoSubscriptionAuth, UsageLimitReached
from hackathon_os.handoff import AgentResult, Status
from hackathon_os.ledger import Ledger, fingerprint
from hackathon_os.llm import AnthropicBackend, SimulatedBackend, pick_backend
from hackathon_os.routing import TIERS, route
from hackathon_os.subscription import SubscriptionBackend, mcp_name
from hackathon_os.tools import ExecutionContext


# ---------------------------------------------------------------------------
# Authentication: subscription only
# ---------------------------------------------------------------------------


def test_paid_credentials_are_hidden_from_the_cli(monkeypatch):
    """A key in the operator's shell must not redirect the run onto paid billing.

    Claude Code ranks ANTHROPIC_API_KEY above the subscription login, so an
    unnoticed export in a profile would silently start charging. We blank it
    for the child process.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-not-a-real-key")
    monkeypatch.delenv(auth.ALLOW_PAID_ENV, raising=False)

    env = auth.child_env()
    assert env["ANTHROPIC_API_KEY"] == ""
    assert "ANTHROPIC_API_KEY" in auth.scrubbable()


def test_every_paid_credential_source_is_scrubbed(monkeypatch):
    monkeypatch.delenv(auth.ALLOW_PAID_ENV, raising=False)
    for var in auth.PAID_ENV_VARS:
        monkeypatch.setenv(var, "x")
    env = auth.child_env()
    assert set(env) == set(auth.PAID_ENV_VARS)
    assert set(env.values()) == {""}


def test_subscription_token_is_never_scrubbed(monkeypatch):
    """CLAUDE_CODE_OAUTH_TOKEN is subscription-backed, so it must survive."""
    monkeypatch.setenv(auth.SUBSCRIPTION_TOKEN_ENV, "tok")
    assert auth.SUBSCRIPTION_TOKEN_ENV not in auth.child_env()


def test_setup_token_counts_as_subscription_auth(monkeypatch, tmp_path):
    """`claude setup-token` is the supported unattended path; accept it."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))  # no login on disk
    monkeypatch.setenv(auth.SUBSCRIPTION_TOKEN_ENV, "tok")
    status = auth.probe(check_cli=False)
    assert status.mechanism == "subscription_token"
    assert status.ok


def test_no_credential_blocks_rather_than_falling_back(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(auth.SUBSCRIPTION_TOKEN_ENV, raising=False)
    monkeypatch.setattr(auth, "_stored_login", lambda: ("", {}))

    status = auth.probe(check_cli=False)
    assert not status.ok
    assert any("subscription" in b for b in status.blockers)
    with pytest.raises(NoSubscriptionAuth):
        auth.require()


def test_api_key_helper_blocks_the_run(monkeypatch, tmp_path):
    """apiKeyHelper outranks the subscription login and cannot be unset by env.

    Working around it would mean running under a credential we cannot classify,
    so it is a blocker, not a warning.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(auth.ALLOW_PAID_ENV, raising=False)
    (tmp_path / "settings.json").write_text(
        json.dumps({"apiKeyHelper": "/usr/local/bin/get-key.sh"}), encoding="utf-8"
    )
    status = auth.probe(check_cli=False)
    assert not status.ok
    assert any("apiKeyHelper" in b for b in status.blockers)


def test_free_plan_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv(auth.SUBSCRIPTION_TOKEN_ENV, raising=False)
    monkeypatch.setattr(auth, "_stored_login", lambda: ("free", {}))
    status = auth.probe(check_cli=False)
    assert not status.ok
    assert any("does not include Claude Code" in b for b in status.blockers)


# ---------------------------------------------------------------------------
# No paid fallback, ever
# ---------------------------------------------------------------------------


def test_auto_never_selects_the_paid_backend(monkeypatch):
    """The old behaviour was 'use the API when a key is present'. It is gone."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-not-a-real-key")
    monkeypatch.setattr(
        "hackathon_os.subscription.SubscriptionBackend.__post_init__", lambda self: None
    )
    backend = pick_backend("auto", verbose=False)
    assert isinstance(backend, SubscriptionBackend)
    assert not isinstance(backend, AnthropicBackend)


def test_paid_backend_needs_two_deliberate_acts(monkeypatch):
    monkeypatch.delenv(auth.ALLOW_PAID_ENV, raising=False)
    with pytest.raises(NoSubscriptionAuth, match="billed"):
        pick_backend("anthropic", verbose=False)


def test_simulated_backend_stays_reachable():
    assert isinstance(pick_backend("simulated", verbose=False), SimulatedBackend)


def test_usage_limit_stops_the_task_instead_of_failing_it(tmp_path):
    """A closed usage window must not become a retryable failure.

    Retrying costs nothing but time when the window is closed, and the only
    other way to make progress is to spend money -- which is exactly what the
    operator ruled out.
    """

    class Limited:
        name = "limited"

        def run(self, **kw):
            raise UsageLimitReached("five_hour window closed")

    spec = get("user_researcher")
    for rel in spec.requires:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("brief", encoding="utf-8")

    with pytest.raises(UsageLimitReached):
        specialist("user_researcher").run(tmp_path, "objective", "", Limited())


# ---------------------------------------------------------------------------
# The access boundary survives the move to the Agent SDK
# ---------------------------------------------------------------------------


def _options(agent: str, tmp_path: Path):
    backend = SubscriptionBackend.__new__(SubscriptionBackend)
    backend.verbose = False
    spec = get(agent)
    ctx = ExecutionContext(root=tmp_path, agent=agent)
    return spec, backend.options_for("system", spec, ctx, route(spec))


def test_specialists_get_no_builtin_file_or_shell_tools(tmp_path):
    """The Agent SDK ships Read/Write/Edit/Bash. None of them reach a specialist.

    If this fails, write scoping is decorative: the Tester could patch the code
    it is testing with the built-in Write and report itself green.
    """
    for agent in REGISTRY:
        _spec, options = _options(agent, tmp_path)
        assert set(options.tools) <= {"WebSearch"}, agent
        assert all(
            t.startswith("mcp__hackathon__") or t == "WebSearch"
            for t in options.allowed_tools
        ), agent


def test_granted_tools_are_exactly_the_spec_allowlist(tmp_path):
    for agent in REGISTRY:
        spec, options = _options(agent, tmp_path)
        expected = {
            mcp_name(t) for t in spec.tools if t != "web_search"
        } | ({"WebSearch"} if "web_search" in spec.tools else set())
        assert set(options.allowed_tools) == expected, agent


def test_operator_settings_are_not_inherited(tmp_path):
    """A specialist's context is what its spec declares, not the operator's.

    Loading filesystem settings would also drag in the one place an
    apiKeyHelper can hide.
    """
    _spec, options = _options("architect", tmp_path)
    assert options.setting_sources == []


def test_tester_cannot_write_source_even_through_the_sdk(tmp_path):
    """The sharpest consequence of the boundary, restated for the new backend."""
    spec, options = _options("tester", tmp_path)
    assert "src/" not in spec.write_paths
    assert mcp_name("write_file") in options.allowed_tools  # it can write...
    assert "Write" not in options.allowed_tools              # ...only through us


# ---------------------------------------------------------------------------
# Model routing
# ---------------------------------------------------------------------------


def test_every_specialist_is_deliberately_routed():
    """A new specialist must be placed, not defaulted onto the Opus window."""
    assert routing.unrouted(REGISTRY) == []


def test_mechanical_roles_do_not_burn_the_opus_window():
    for agent in ("submission_manager", "presentation_builder", "brand_designer"):
        assert route(get(agent)).model == TIERS["light"].model


def test_decisions_everything_depends_on_get_the_best_model():
    for agent in ("architect", "strategist", "final_auditor"):
        assert route(get(agent)).model == TIERS["deep"].model


def test_failed_attempts_escalate_the_tier():
    spec = get("market_researcher")
    first = route(spec, attempt=0)
    second = route(spec, attempt=1)
    assert routing.ORDER.index(second.name) > routing.ORDER.index(first.name)


def test_escalation_stops_at_the_top():
    spec = get("architect")
    assert route(spec, attempt=5).name == "deep"


def test_a_stale_spec_pin_cannot_escalate_cost():
    """A pinned model may only ever make a role cheaper, never more expensive."""
    for agent in REGISTRY:
        spec = get(agent)
        routed = route(spec)
        table = TIERS[routing.tier_name(agent)]
        assert routing.ORDER.index(routed.name) <= routing.ORDER.index(table.name)


def test_a_whole_run_can_be_forced_onto_one_tier(monkeypatch):
    monkeypatch.setenv(routing.FORCE_TIER_ENV, "standard")
    assert route(get("architect")).model == TIERS["standard"].model


# ---------------------------------------------------------------------------
# Deduplication and the ledger
# ---------------------------------------------------------------------------


def _brief(root: Path, spec) -> None:
    for rel in spec.requires:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("the brief", encoding="utf-8")


def test_fingerprint_follows_input_content_not_mtime(tmp_path):
    spec = get("user_researcher")
    _brief(tmp_path, spec)
    tier = route(spec)
    before = fingerprint(spec, tier, tmp_path, "obj", "ctx")

    (tmp_path / spec.requires[0]).write_text("the brief", encoding="utf-8")  # touch
    assert fingerprint(spec, tier, tmp_path, "obj", "ctx") == before

    (tmp_path / spec.requires[0]).write_text("a different brief", encoding="utf-8")
    assert fingerprint(spec, tier, tmp_path, "obj", "ctx") != before


def test_fingerprint_separates_tiers(tmp_path):
    spec = get("user_researcher")
    _brief(tmp_path, spec)
    a = fingerprint(spec, TIERS["light"], tmp_path, "obj", "ctx")
    b = fingerprint(spec, TIERS["deep"], tmp_path, "obj", "ctx")
    assert a != b


def test_completed_work_replays_without_a_model_call(tmp_path):
    """The point of the ledger: resuming a run must not redo finished work."""

    class Exploding:
        name = "exploding"

        def run(self, **kw):
            raise AssertionError("the backend must not be reached on a cache hit")

    spec = get("user_researcher")
    _brief(tmp_path, spec)
    (tmp_path / "RESEARCH").mkdir(exist_ok=True)
    artifact = tmp_path / spec.produces[0]
    artifact.write_text("# done\n" + "word " * 400, encoding="utf-8")

    led = Ledger.load(tmp_path)
    fp = fingerprint(spec, route(spec), tmp_path, "obj", "ctx")
    led.record(fp, spec, route(spec), AgentResult(
        status=Status.COMPLETED, agent=spec.name, summary="first run",
    ))

    result = specialist("user_researcher").run(
        tmp_path, "obj", "ctx", Exploding(), ledger=led,
    )
    assert result.status is Status.COMPLETED
    assert any("ledger" in n for n in result.notes)
    assert led.hits == 1


def test_a_modified_artifact_invalidates_the_entry(tmp_path):
    spec = get("user_researcher")
    _brief(tmp_path, spec)
    (tmp_path / "RESEARCH").mkdir(exist_ok=True)
    artifact = tmp_path / spec.produces[0]
    artifact.write_text("original", encoding="utf-8")

    led = Ledger.load(tmp_path)
    fp = fingerprint(spec, route(spec), tmp_path, "obj", "ctx")
    led.record(fp, spec, route(spec), AgentResult(status=Status.COMPLETED, agent=spec.name))
    assert led.lookup(fp) is not None

    artifact.write_text("someone edited this by hand", encoding="utf-8")
    assert led.lookup(fp) is None


def test_a_deleted_artifact_invalidates_the_entry(tmp_path):
    spec = get("user_researcher")
    _brief(tmp_path, spec)
    (tmp_path / "RESEARCH").mkdir(exist_ok=True)
    artifact = tmp_path / spec.produces[0]
    artifact.write_text("original", encoding="utf-8")

    led = Ledger.load(tmp_path)
    fp = fingerprint(spec, route(spec), tmp_path, "obj", "ctx")
    led.record(fp, spec, route(spec), AgentResult(status=Status.COMPLETED, agent=spec.name))
    artifact.unlink()
    assert led.lookup(fp) is None


def test_failures_are_never_cached(tmp_path):
    spec = get("user_researcher")
    led = Ledger.load(tmp_path)
    fp = "deadbeef"
    led.record(fp, spec, route(spec), AgentResult(status=Status.FAILED, agent=spec.name))
    assert led.lookup(fp) is None


def test_editing_a_mission_invalidates_its_cached_work(tmp_path):
    """A prompt change must not be hidden by the cache -- that is the one thing
    the operator is most likely to be testing."""
    import copy

    spec = get("technical_writer")
    _brief(tmp_path, spec)
    tier = route(spec)
    before = fingerprint(spec, tier, tmp_path, "obj", "ctx")

    edited = copy.copy(spec)
    edited.mission = spec.mission + "\n\nAlso mention the deployment story."
    assert fingerprint(edited, tier, tmp_path, "obj", "ctx") != before


def test_the_ledger_survives_corruption(tmp_path):
    """A bad ledger costs a re-run. It must never take the hackathon down."""
    (tmp_path / "AGENT" / "cache").mkdir(parents=True)
    (tmp_path / "AGENT" / "cache" / "ledger.json").write_text("{not json", encoding="utf-8")
    led = Ledger.load(tmp_path)
    assert led.entries == {}


def test_disabling_the_cache_disables_lookups(tmp_path):
    spec = get("user_researcher")
    led = Ledger.load(tmp_path, enabled=False)
    led.record("fp", spec, route(spec), AgentResult(status=Status.COMPLETED))
    assert led.lookup("fp") is None


# ---------------------------------------------------------------------------
# Prompt shape
# ---------------------------------------------------------------------------


def test_the_artifact_contract_is_stated_before_the_work(tmp_path):
    """Learning a postcondition by failing it costs an entire extra model run."""
    prompt = specialist("user_researcher").system_prompt()
    assert "How your work is checked" in prompt
    for needle in ("target user", "pain point"):
        assert needle in prompt


def test_every_specialist_states_its_contract():
    for agent in REGISTRY:
        prompt = specialist(agent).system_prompt()
        assert "How your work is checked" in prompt
        assert "(no automated checks)" not in prompt or not get(agent).produces


def test_outcome_is_unchanged_for_the_backend_contract():
    """SubscriptionBackend must keep returning what the runner already parses."""
    out = _Outcome(text="x", error="", input_tokens=1, output_tokens=2)
    assert (out.text, out.error, out.input_tokens, out.output_tokens) == ("x", "", 1, 2)


# ---------------------------------------------------------------------------
# The scenario the whole design is for: the window closes mid-run
# ---------------------------------------------------------------------------


def _project(tmp_path: Path):
    from hackathon_os.state import ProjectState

    return ProjectState.create(
        tmp_path / "p",
        "limits",
        problem="Build a tool that helps hackathon judges score consistently.",
        judging="Innovation 40%, Execution 40%, Presentation 20%",
        submission="Public repo and a zip",
        constraints="Python only",
    )


class _StopsAfter:
    """A backend that works N times, then hits the plan's usage limit."""

    name = "stops-after"

    def __init__(self, n: int) -> None:
        self.n = n
        self.calls = 0

    def run(self, **kw):
        self.calls += 1
        if self.calls > self.n:
            raise UsageLimitReached("five_hour window closed")
        return SimulatedBackend(verbose=False).run(**kw)


def test_a_usage_limit_halts_the_run_and_resume_replays_the_finished_work(tmp_path):
    """The end-to-end promise: stop rather than spend, lose nothing, resume free.

    When the limit fires mid-wave, `step` never reaches `graph.record`, so the
    siblings that *had* finished are lost from the task graph and from history.
    That is precisely why the ledger is keyed on content rather than on state:
    those tasks come back as pending with their inputs and context unchanged, so
    they fingerprint identically and replay for free. A backend that raises if
    reached proves no model call is made.

    Note the honest limit of this: a task whose context genuinely changed --
    because earlier results were recorded and now appear in its context digest
    -- fingerprints differently and is re-run. The cache never hides a real
    change in the work.
    """
    from hackathon_os.orchestrator import Orchestrator

    state = _project(tmp_path)
    orch = Orchestrator(state, _StopsAfter(3), parallel=1, verbose=False)
    orch.plan(depth="lean")
    orch.run(max_waves=6)

    assert orch.limit_hit is not None, "the run should have halted on the limit"
    assert orch.ledger.entries, "work finished before the limit must be recorded"

    # Work that completed inside the aborted wave: in the ledger, but the graph
    # never heard about it, so it is pending again.
    recorded = {e.task_id for e in orch.ledger.entries.values()}
    pending = [t for t in state.graph.tasks.values()
               if t.status is None and t.id in recorded]
    assert pending, "the aborted wave should leave finished work pending"

    class Exploding:
        name = "exploding"

        def run(self, **kw):
            raise AssertionError("replayed work must not reach the model")

    resumed = Orchestrator(state, Exploding(), parallel=1, verbose=False)
    for task in pending:
        result = resumed.run_task(task)
        assert result.status is Status.COMPLETED, task.id
        assert any("ledger" in n for n in result.notes), task.id
    assert resumed.ledger.hits == len(pending)


def test_the_ledger_never_hides_a_changed_brief(tmp_path):
    """Editing the problem statement must invalidate work that read it.

    The opposite failure -- a cache that serves stale work after the brief
    changed -- would be far worse than a redundant run, because it is silent.
    """
    from hackathon_os.orchestrator import Orchestrator

    state = _project(tmp_path)
    orch = Orchestrator(state, SimulatedBackend(verbose=False), parallel=1, verbose=False)
    orch.plan(depth="lean")
    first = orch.next_actions()[0]
    orch.run_task(first)
    assert orch.ledger.entries

    spec = get(first.agent)
    brief = state.root / spec.requires[0]
    brief.write_text(
        brief.read_text(encoding="utf-8") + "\n\nNew constraint: offline only.",
        encoding="utf-8",
    )

    reread = Orchestrator(state, SimulatedBackend(verbose=False), parallel=1, verbose=False)
    context = reread.ctxb.build(spec.context_keys, recent=state.history)
    assert reread.ledger.lookup(
        fingerprint(spec, route(spec), state.root, first.objective, context)
    ) is None
