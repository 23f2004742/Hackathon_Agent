"""LLM backends.

`SubscriptionBackend` (in `subscription.py`) is the real one, and the default:
the Claude Agent SDK driving the specialists on the operator's own Claude
subscription, with no API key anywhere. `pick_backend` will not choose
anything else.

`AnthropicBackend` is the older paid path -- the API SDK's tool_runner loop,
with the resilience the scaffold worked out (history mirroring, pause_turn
restarts, refusal fallback, typed error handling). It is kept because that
resilience was expensive to learn and is worth not deleting, but it bills a
Console API key per token, so reaching it now takes two explicit acts: naming
the backend *and* setting HACKATHON_ALLOW_PAID_API=1.

`SimulatedBackend` runs the same specialists against the same tool layer with
no API. It exists so the orchestration machinery -- task graph, access
boundaries, artifact contracts, packaging -- can be tested end to end
deterministically and for free. It is NOT a model: it produces structurally
valid documents, not insightful ones. Anything it writes is scaffolding, and
BUILD_REPORT.md says so plainly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .agents.base import _Outcome
from .tools import ExecutionContext

from .glyphs import BLUE, GREY, RED, RESET, YELLOW


class Backend:
    """Interface the Specialist runner calls."""

    name = "base"

    def run(self, *, system: str, user: str, tools: list, spec, ctx) -> _Outcome:
        raise NotImplementedError

    def ask_json(self, *, system: str, user: str, purpose: str = "") -> str:
        """One toolless question, answered as JSON text.

        This is the planning channel, kept deliberately separate from `run`:
        no tools, no write scope, no project on disk. The capability planner
        and anything else that needs a structured judgement uses it, and a
        backend that cannot answer says so rather than pretending.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Real
# ---------------------------------------------------------------------------


class AnthropicBackend(Backend):
    """The production backend: Claude driving the tools itself."""

    name = "anthropic"
    MAX_PAUSE_RESTARTS = 5

    def __init__(self, *, verbose: bool = True, use_refusal_fallback: bool = True) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.verbose = verbose
        self.use_refusal_fallback = use_refusal_fallback

    @staticmethod
    def available() -> bool:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _show(self, message: Any) -> None:
        if not self.verbose:
            return
        for block in message.content:
            if block.type == "thinking" and getattr(block, "thinking", ""):
                first = block.thinking.strip().splitlines()[0][:110]
                print(f"      {GREY}{first}{RESET}")
            elif block.type == "text" and block.text.strip():
                print(f"      {block.text.strip()[:200]}")
            elif block.type == "tool_use":
                arg = next(iter(block.input.values()), "") if block.input else ""
                print(f"      {BLUE}-> {block.name}{RESET} {str(arg)[:80].replace(chr(10), ' ')}")
            elif block.type == "server_tool_use":
                print(f"      {BLUE}-> {block.name}{RESET} {str(block.input)[:80]}")

    def ask_json(self, *, system: str, user: str, purpose: str = "") -> str:
        """A single toolless completion, for planning."""
        msg = self.client.messages.create(
            model="claude-sonnet-5",
            max_tokens=4_000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    def run(self, *, system: str, user: str, tools: list, spec, ctx: ExecutionContext) -> _Outcome:
        a = self._anthropic
        messages: list = [{"role": "user", "content": user}]
        text_out, tin, tout = [], 0, 0

        for _ in range(self.MAX_PAUSE_RESTARTS + 1):
            kwargs = dict(
                model=spec.model,
                max_tokens=spec.max_tokens,
                system=system,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": spec.effort},
                tools=tools,
                messages=messages,
            )
            if self.use_refusal_fallback:
                kwargs["betas"] = ["server-side-fallback-2026-07-01"]
                kwargs["fallbacks"] = "default"

            try:
                runner = self.client.beta.messages.tool_runner(**kwargs)
                last = None
                for message in runner:
                    last = message
                    self._show(message)
                    if getattr(message, "usage", None):
                        tin += getattr(message.usage, "input_tokens", 0) or 0
                        tout += getattr(message.usage, "output_tokens", 0) or 0
                    for b in message.content:
                        if b.type == "text" and b.text.strip():
                            text_out.append(b.text.strip())
                    messages.append({"role": "assistant", "content": message.content})
                    tool_response = runner.generate_tool_call_response()
                    if tool_response is not None:
                        messages.append(tool_response)
                    # The agent has reported; stop burning turns.
                    if ctx.handoff is not None:
                        return _Outcome("\n".join(text_out), "", tin, tout)
            except a.BadRequestError as e:
                if self.use_refusal_fallback and "fallback" in str(e).lower():
                    self.use_refusal_fallback = False
                    continue
                return _Outcome("\n".join(text_out), f"bad request: {e}", tin, tout)
            except a.RateLimitError as e:
                retry = e.response.headers.get("retry-after", "?")
                return _Outcome("\n".join(text_out), f"rate limited (retry after {retry}s)", tin, tout)
            except a.APIStatusError as e:
                return _Outcome("\n".join(text_out), f"API error {e.status_code}: {e.message}", tin, tout)
            except a.APIConnectionError:
                return _Outcome("\n".join(text_out), "network error reaching the API", tin, tout)

            if last is None:
                return _Outcome("\n".join(text_out), "no response from the model", tin, tout)
            if last.stop_reason == "refusal":
                detail = getattr(last.stop_details, "explanation", None) or "no detail given"
                return _Outcome("\n".join(text_out), f"model declined: {detail}", tin, tout)
            if last.stop_reason != "pause_turn":
                return _Outcome("\n".join(text_out), "", tin, tout)

        return _Outcome("\n".join(text_out), "turn still paused after restarts", tin, tout)


# ---------------------------------------------------------------------------
# Simulated
# ---------------------------------------------------------------------------


@dataclass
class SimulatedBackend(Backend):
    """Deterministic stand-in that exercises the real tool layer.

    It drives the same sequence a competent specialist would: read the required
    inputs, consult prior art, write each declared artifact, report a handoff.
    Every step goes through the actual tool functions, so access boundaries,
    write scoping and artifact contracts are genuinely tested -- only the
    judgement is absent.
    """

    name: str = "simulated"
    verbose: bool = True

    def ask_json(self, *, system: str, user: str, purpose: str = "") -> str:
        """A deterministic stand-in for the capability planner.

        It re-derives the answer from the same rule engine rather than
        inventing one, so a simulated run exercises the whole two-stage merge
        path -- prompt assembly, JSON parsing, guardrails -- without ever
        claiming to have model judgement it does not have.
        """
        import json as _json
        import re as _re

        from .planner import analyse, select_by_rules

        m = _re.search(r"## Brief(.+?)## Required output", user, _re.S)
        brief = (m.group(1) if m else user).strip()
        a = analyse(brief)
        sel = select_by_rules(brief, analysis=a)
        return _json.dumps({
            "project_type": a.project_type,
            "complexity": a.complexity,
            "required_specialists": [
                {"agent": n, "reason": sel.reasons.get(n, ""),
                 "priority": sel.choices[n].priority if n in sel.choices else "medium",
                 "estimated_effort": "medium"}
                for n in sorted(sel.chosen)
            ],
            "optional_specialists": [],
            "excluded_specialists": [
                {"agent": n, "reason": why}
                for n, why in sorted(sel.skipped.items()) if n not in sel.chosen
            ],
            "notes": "simulated planner: derived from deterministic rules, not model judgement",
        })

    def run(self, *, system: str, user: str, tools: list, spec, ctx: ExecutionContext) -> _Outcome:
        from .simulation import synthesize
        from .tools import REGISTRY

        def call(tool_name: str, **kw) -> str:
            if tool_name not in spec.tools:
                return f"(skipped: {spec.name} has no {tool_name})"
            out = REGISTRY[tool_name].fn(**kw)
            if self.verbose:
                arg = next(iter(kw.values()), "")
                print(f"      {BLUE}-> {tool_name}{RESET} {str(arg)[:70].replace(chr(10), ' ')}")
            return out

        inputs: dict[str, str] = {}
        for rel in spec.requires:
            inputs[rel] = call("read_file", path=rel)

        prior = ""
        if "knowledge_search" in spec.tools:
            prior = call("knowledge_search", query=f"{spec.team} {spec.title}")

        # Produce every declared artifact through the real write path.
        for rel in spec.produces:
            body = synthesize(spec, rel, ctx.root, inputs, prior)
            if rel.endswith(".json"):
                call("write_file", path=rel, content=body)
            elif rel.endswith(".pptx"):
                src = "PRESENTATION/slides.md"
                if (ctx.root / src).is_file():
                    call("build_pptx", slides_markdown=src, output=rel)
            elif rel.endswith(".zip"):
                call("build_zip", output=rel, include="**/*.md,**/*.json,**/*.py,**/*.txt")
            else:
                call("write_file", path=rel, content=body)

        # Packaging is not an artifact contract -- a hackathon that wants a
        # GitHub link should not be forced to emit a zip -- but when the agent
        # holds build_zip and the brief asks for an archive, exercise it.
        if "build_zip" in spec.tools:
            brief = (ctx.root / "AGENT/submission_requirements.md")
            wants_zip = brief.is_file() and ".zip" in brief.read_text(
                encoding="utf-8", errors="replace"
            ).lower()
            if wants_zip:
                call(
                    "build_zip",
                    output="SUBMISSION/submission.zip",
                    include="**/*.md,**/*.json,**/*.py,**/*.html,**/*.pptx,**/*.txt",
                    exclude="AGENT/state.json",
                )

        call(
            "submit_handoff",
            status="completed",
            summary=(
                f"{spec.title} completed its task using the simulated backend. "
                f"Produced {len(spec.produces)} artifact(s). "
                "Content is structural scaffolding, not model-authored analysis."
            ),
            findings_json='[{"summary":"Simulated run: content is scaffolding, not analysis.",'
                          '"severity":"INFO","source":"SimulatedBackend"}]',
            assumptions_json='["Ran without an LLM; artifact content is templated."]',
        )
        return _Outcome(text=f"{spec.title} (simulated)")


def pick_backend(mode: str = "auto", *, verbose: bool = True) -> Backend:
    """Choose a backend.

    `auto` means the operator's Claude subscription, and nothing else. There is
    deliberately no path from here to a paid API key: if subscription auth is
    unavailable this raises, because the two things a user who asked for
    "subscription only" would least want are a surprise bill and a silent
    downgrade to templated output.
    """
    from .auth import ALLOW_PAID_ENV, NoSubscriptionAuth
    from .subscription import SubscriptionBackend

    if mode == "simulated":
        return SimulatedBackend(verbose=verbose)

    if mode == "anthropic":
        # Paid, per-token, Console-billed. Reachable only when the operator has
        # said so twice: by naming the backend and by setting the env var.
        if os.environ.get(ALLOW_PAID_ENV) != "1":
            raise NoSubscriptionAuth(
                "the 'anthropic' backend bills a paid API key per token. This "
                f"system runs on your subscription by default. Set {ALLOW_PAID_ENV}=1 "
                "if you genuinely want to be billed for API usage."
            )
        print(f"  {YELLOW}paid API backend selected -- this run will be billed per token.{RESET}")
        return AnthropicBackend(verbose=verbose)

    backend = SubscriptionBackend(verbose=verbose)
    if verbose:
        print(backend.status.render())
    return backend
