"""The subscription backend: Claude Agent SDK, no API key, boundaries intact.

The Agent SDK is Claude Code packaged as a library. It spawns the Claude Code
CLI, and the CLI authenticates with whatever credential the operator already
has -- for a Pro/Max/Team account, the subscription OAuth login. That is the
officially supported way to drive Claude from a subscription, and it is why
this system needs no API key and makes no billable request.

The risk in adopting it is that the SDK arrives with its own agent loop *and
its own built-in tools* -- Read, Write, Edit, Bash, Glob, Grep. Handing those
to a specialist would quietly destroy the thing this OS is actually built on:
the Tester would be able to patch `src/` with the built-in Write and make its
own report green, whatever its declared write scope says.

So the built-in tool surface is switched off entirely (`tools=[]`) and each
specialist is handed exactly its allowlisted tools, re-exposed as an in-process
MCP server. Those tools are the same functions the API backend called, running
under the same `ExecutionContext`, so `guard()`, `resolve_for_write()` and the
artifact contract all still apply. The model cannot call what it was never
sent, and the session `init` message is asserted against the allowlist in
`tests/test_subscription.py` so this stays true.

The one built-in kept is `WebSearch`, and only for specialists whose spec
already declares the `web_search` server tool.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from . import auth
from .agents.base import _Outcome
from .auth import UsageLimitReached
from .glyphs import BLUE, GREY, RED, RESET, YELLOW
from .llm import Backend
from .routing import Tier, route
from .tools import REGISTRY, SERVER_TOOLS, ExecutionContext

MCP_SERVER = "hackathon"

# Claude Code's own tools, mapped from the names our specs already use.
BUILTIN_FOR_SERVER_TOOL = {"web_search": "WebSearch"}


def mcp_name(tool: str) -> str:
    return f"mcp__{MCP_SERVER}__{tool}"


def _sdk_tools(names: tuple[str, ...]) -> list:
    """Re-expose this specialist's allowlisted tools as in-process MCP tools.

    The wrapper is thin on purpose: it forwards to the identical function the
    registry holds, so every access check, write-scope resolution and audit note
    happens in exactly the same code the test suite already covers.
    """
    from claude_agent_sdk import tool as sdk_tool

    out = []
    for name in names:
        if name in SERVER_TOOLS:
            continue  # served by a Claude Code built-in, not by us
        spec = REGISTRY[name]

        def make(registered):
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                # Called on the loop's own thread, which is the thread that
                # entered `using(ctx)` -- so the thread-local context is ours.
                try:
                    text = registered.fn(**args)
                except Exception as e:  # noqa: BLE001 - a tool error is a result
                    text = f"ERROR ({type(e).__name__}): {e}"
                return {"content": [{"type": "text", "text": str(text)}]}
            return handler

        out.append(
            sdk_tool(spec.name, spec.summary or spec.name, spec.fn.input_schema)(
                make(spec)
            )
        )
    return out


@dataclass
class _Turn:
    """What one specialist's session produced, before it becomes an _Outcome."""

    text: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    error: str = ""
    turns: int = 0
    session_id: str = ""
    granted: list[str] = field(default_factory=list)


@dataclass
class SubscriptionBackend(Backend):
    """Runs specialists on the operator's Claude subscription via the Agent SDK."""

    name: str = "subscription"
    verbose: bool = True
    status: auth.AuthStatus | None = None
    # Set when the plan's usage limit rejects a request, so the orchestrator can
    # stop the whole run instead of burning retries against a closed window.
    limit: Any = None

    def __post_init__(self) -> None:
        if self.status is None:
            self.status = auth.require()

    @staticmethod
    def available() -> bool:
        return auth.probe().ok

    # -- the Backend interface --------------------------------------------

    def run(self, *, system: str, user: str, tools: list, spec, ctx: ExecutionContext) -> _Outcome:
        # `tools` is the API-shaped list the older backend wanted. The Agent SDK
        # takes names, so the spec's allowlist is the input here.
        tier = getattr(ctx, "tier", None) or route(spec)
        turn = asyncio.run(self._session(system, user, spec, ctx, tier))
        return _Outcome(
            text="\n".join(turn.text),
            error=turn.error,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )

    def ask_json(self, *, system: str, user: str, purpose: str = "") -> str:
        """One toolless planning call on the subscription.

        No MCP server, no built-ins, no cwd write scope, one turn: this must
        not be able to touch the project, and it must not be able to spend a
        rate-limit window on an agent loop. It is the cheapest useful call the
        system makes, and it is what lets the run skip eight specialists.
        """
        return asyncio.run(self._ask(system, user, purpose))

    async def _ask(self, system: str, user: str, purpose: str) -> str:
        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, query

        options = ClaudeAgentOptions(
            model=self._planner_model(),
            effort="high",
            max_turns=1,
            tools=[],
            allowed_tools=[],
            permission_mode="default",
            setting_sources=[],
            system_prompt=system,
            env=auth.child_env(),
            stderr=self._stderr,
        )
        if self.verbose:
            print(f"    {GREY}asking Claude for the {purpose or 'plan'}...{RESET}")
        chunks: list[str] = []
        stream = query(prompt=user, options=options)
        try:
            async for message in stream:
                self._check_limits(message, _Turn())
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        text = getattr(block, "text", "")
                        if text and text.strip():
                            chunks.append(text)
        except UsageLimitReached:
            raise
        except Exception:  # noqa: BLE001 - planning is best-effort
            return "\n".join(chunks)
        finally:
            with suppress(Exception):
                await stream.aclose()
        return "\n".join(chunks)

    @staticmethod
    def _planner_model() -> str:
        """The planner runs on the default model, never the Opus window.

        Staffing a team is a judgement, but it is a small one over a short
        brief, and spending the plan's separate weekly Opus budget on it would
        defeat the point of having a planner at all.
        """
        from .model_planner import resolve
        return resolve("default")

    # -- the session -------------------------------------------------------

    def options_for(self, system: str, spec, ctx: ExecutionContext, tier: Tier):
        """Build the SDK options for one specialist.

        Separated from the session so the access boundary can be asserted
        without spending a model call: `tests/test_subscription.py` checks that
        the built-in tool surface is empty and that `allowed_tools` is exactly
        this specialist's allowlist.
        """
        from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server

        allowed = [mcp_name(t) for t in spec.tools if t not in SERVER_TOOLS]
        builtins = [
            BUILTIN_FOR_SERVER_TOOL[t]
            for t in spec.tools
            if t in SERVER_TOOLS and t in BUILTIN_FOR_SERVER_TOOL
        ]
        server = create_sdk_mcp_server(
            name=MCP_SERVER, version="1.0.0", tools=_sdk_tools(spec.tools)
        )
        return ClaudeAgentOptions(
            model=tier.model,
            effort=tier.effort,
            thinking={"type": "adaptive"},
            max_turns=tier.max_turns,
            # The whole boundary argument in one line: no built-in file, shell
            # or edit tools reach the specialist.
            tools=builtins,
            mcp_servers={MCP_SERVER: server},
            allowed_tools=allowed + builtins,
            permission_mode="default",
            # Do not inherit the operator's settings, memory or skills: they are
            # not part of this specialist's declared context, they cost tokens,
            # and settings.json is where an apiKeyHelper would hide.
            setting_sources=[],
            system_prompt=system,
            cwd=str(ctx.root),
            # Hide every paid credential from the CLI subprocess.
            env=auth.child_env(),
            stderr=self._stderr,
        )

    async def _session(self, system: str, user: str, spec, ctx: ExecutionContext, tier: Tier) -> _Turn:
        from claude_agent_sdk import (
            AssistantMessage, ResultMessage, SystemMessage, query,
        )

        turn = _Turn()
        options = self.options_for(system, spec, ctx, tier)

        # Held explicitly so it can be closed on the way out. Leaving an
        # abandoned `async for` to the loop's shutdown hook races the generator
        # and raises "aclose(): asynchronous generator is already running",
        # which looks like a crash in a run that actually succeeded.
        stream = query(prompt=user, options=options)
        try:
            async for message in stream:
                self._absorb(message, turn, SystemMessage, AssistantMessage, ResultMessage)
                self._check_limits(message, turn)
                # The specialist has filed its report; every further turn is
                # spend against a rate-limit window for nothing.
                if ctx.handoff is not None:
                    break
        except UsageLimitReached:
            raise
        except Exception as e:  # noqa: BLE001 - a crashed session is a failed task
            turn.error = turn.error or f"{type(e).__name__}: {e}"
        finally:
            with suppress(Exception):
                await stream.aclose()
        return turn

    # -- message handling ---------------------------------------------------

    def _absorb(self, message, turn: _Turn, SystemMessage, AssistantMessage, ResultMessage) -> None:
        if isinstance(message, SystemMessage):
            if message.subtype == "init":
                turn.granted = list(message.data.get("tools") or [])
                turn.session_id = str(message.data.get("session_id") or "")
            return

        if isinstance(message, AssistantMessage):
            for block in message.content:
                text = getattr(block, "text", "")
                thinking = getattr(block, "thinking", "")
                if text and text.strip():
                    turn.text.append(text.strip())
                    if self.verbose:
                        print(f"      {text.strip()[:200]}")
                elif thinking and self.verbose:
                    first = thinking.strip().splitlines()[0][:110]
                    print(f"      {GREY}{first}{RESET}")
                elif getattr(block, "name", ""):
                    if self.verbose:
                        raw = getattr(block, "input", None) or {}
                        arg = next(iter(raw.values()), "") if raw else ""
                        short = str(block.name).replace(f"mcp__{MCP_SERVER}__", "")
                        print(f"      {BLUE}-> {short}{RESET} "
                              f"{str(arg)[:80].replace(chr(10), ' ')}")
            return

        if isinstance(message, ResultMessage):
            usage = message.usage or {}
            turn.input_tokens += int(usage.get("input_tokens") or 0)
            turn.input_tokens += int(usage.get("cache_read_input_tokens") or 0)
            turn.input_tokens += int(usage.get("cache_creation_input_tokens") or 0)
            turn.output_tokens += int(usage.get("output_tokens") or 0)
            turn.turns = message.num_turns
            if message.is_error and not turn.error:
                turn.error = self._describe(message)

    def _describe(self, message) -> str:
        detail = "; ".join(message.errors or []) or message.result or message.subtype
        if message.api_error_status:
            return f"session failed ({message.api_error_status}): {detail}"
        return f"session failed: {detail}"

    def _check_limits(self, message, turn: _Turn) -> None:
        """Stop the run when the plan's usage window is closed.

        This is the point of the whole design: the limit is a subscription
        limit, and hitting it must stop work, not silently start spending money.
        """
        info = getattr(message, "rate_limit_info", None)
        if info is None:
            return
        window = info.rate_limit_type or "usage"
        if info.status == "allowed_warning" and self.verbose:
            pct = f"{info.utilization:.0%}" if info.utilization is not None else "?"
            print(f"    {YELLOW}approaching your {window} limit ({pct} used){RESET}")
        if info.status != "rejected":
            return
        self.limit = info
        when = _reset_text(info.resets_at)
        raise UsageLimitReached(
            f"your Claude plan's {window} usage limit is reached{when}. "
            "Stopping. Nothing here falls back to a paid API, and paid overage "
            "is not enabled by this system. Resume with the same command once "
            "the window resets -- completed tasks replay from the ledger."
        )

    def _stderr(self, line: str) -> None:
        if self.verbose and ("error" in line.lower() or "warn" in line.lower()):
            print(f"      {RED}{line.strip()[:160]}{RESET}")


def _reset_text(resets_at: int | None) -> str:
    if not resets_at:
        return ""
    from datetime import datetime
    try:
        when = datetime.fromtimestamp(resets_at).astimezone()
    except (OSError, OverflowError, ValueError):
        return ""
    return f" (resets {when:%Y-%m-%d %H:%M %Z})"
