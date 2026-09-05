"""Tool registry and the access boundary that makes specialists real.

Two things happen here that a system-prompt-only "multi-agent" setup cannot do:

1. **Tool allowlisting.** Each specialist is handed only the tools its spec
   names. A Market Researcher is never given `run_shell` -- the model cannot
   call what it was not sent.

2. **Write scoping.** Every write resolves against the agent's declared
   `write_paths`. A researcher writing to `src/` is rejected at the filesystem
   layer with an error string the model can read and recover from. This is
   enforced here, in code, so it holds even if a prompt is ignored.

Both are checked against the *active* ExecutionContext, which the agent runner
swaps per task. Tools consult it rather than receiving it as an argument,
because the SDK derives each tool's JSON schema from its signature and we do
not want plumbing in the model-facing contract.
"""

from __future__ import annotations

import fnmatch
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from anthropic import beta_tool

MAX_RESULT_CHARS = 30_000


class ToolDenied(Exception):
    """Raised inside a tool when the active context forbids the operation."""


@dataclass
class ExecutionContext:
    """Who is running, where they may write, and what they may call."""

    root: Path
    agent: str = "system"
    task_id: str = ""
    write_paths: tuple[str, ...] = ()
    allowed_tools: frozenset[str] = frozenset()
    auto_approve: bool = True
    dry_run: bool = False
    # The routing.Tier this task was assigned. Carried here rather than passed
    # through the Backend signature because it is a property of *this run of
    # this agent*, which is exactly what the context already describes.
    tier: Any = None
    # Populated by tools as they run; the runner folds these into AgentResult.
    tool_calls: int = 0
    audit: list[str] = field(default_factory=list)
    # Filled in by the submit_handoff tool; the runner reads it back.
    handoff: dict | None = None

    def __post_init__(self) -> None:
        # A relative root compares false against every resolved path, which
        # rejects the whole project. Normalise once, here, rather than trusting
        # every caller to pass an absolute path.
        self.root = Path(self.root).resolve()

    def resolve(self, path: str) -> Path:
        """Resolve a model-supplied path inside the project root, or raise."""
        p = Path(path)
        p = p.resolve() if p.is_absolute() else (self.root / p).resolve()
        if not p.is_relative_to(self.root):
            raise ToolDenied(f"path escapes the project root ({self.root}): {path}")
        return p

    def resolve_for_write(self, path: str) -> Path:
        """Resolve a path the caller intends to write, honouring write scope."""
        p = self.resolve(path)
        rel = p.relative_to(self.root).as_posix()
        if not self.write_paths:
            raise ToolDenied(
                f"{self.agent} has no write permission anywhere in this project."
            )
        for pattern in self.write_paths:
            # A bare directory prefix means "anything under it".
            if rel == pattern or rel.startswith(pattern.rstrip("/") + "/"):
                return p
            if fnmatch.fnmatch(rel, pattern):
                return p
        raise ToolDenied(
            f"{self.agent} may not write to '{rel}'. "
            f"Its write scope is: {', '.join(self.write_paths)}. "
            f"If this file is genuinely yours to produce, say so in your summary "
            f"and hand the task to the specialist that owns that path."
        )

    def note(self, line: str) -> None:
        self.audit.append(line)


# The active context. A thread-local so parallel task execution keeps its own.
_local = threading.local()


def active() -> ExecutionContext:
    ctx = getattr(_local, "ctx", None)
    if ctx is None:
        raise ToolDenied("no active execution context; tools cannot run bare.")
    return ctx


@contextmanager
def using(ctx: ExecutionContext) -> Iterator[ExecutionContext]:
    previous = getattr(_local, "ctx", None)
    _local.ctx = ctx
    try:
        yield ctx
    finally:
        _local.ctx = previous


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass
class ToolSpec:
    name: str
    category: str
    writes: bool
    approval: bool
    fn: Any  # BetaFunctionTool -- callable, and carries .input_schema
    summary: str

    def __call__(self, *a: Any, **kw: Any) -> str:
        return self.fn(*a, **kw)


REGISTRY: dict[str, ToolSpec] = {}


def tool(category: str, *, writes: bool = False, approval: bool = False) -> Callable:
    """Register a function as a tool.

    The docstring is the prompt the model reads to decide when to call this, so
    write it for the model. `writes` marks tools that mutate the project;
    `approval` marks those that need a human gate when not auto-approving.
    """

    def decorate(fn: Callable) -> ToolSpec:
        wrapped = beta_tool(fn)
        spec = ToolSpec(
            name=wrapped.name,
            category=category,
            writes=writes,
            approval=approval,
            fn=wrapped,
            summary=(fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
        )
        REGISTRY[spec.name] = spec
        return spec

    return decorate


def belt(names: list[str]) -> list[Any]:
    """The API-facing tool list for one specialist: allowlist -> SDK objects."""
    out = []
    for n in names:
        spec = REGISTRY.get(n)
        if spec is None:
            raise KeyError(f"unknown tool '{n}' (registered: {sorted(REGISTRY)})")
        out.append(spec.fn)
    return out


def categories() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, spec in sorted(REGISTRY.items()):
        out.setdefault(spec.category, []).append(name)
    return out


# ---------------------------------------------------------------------------
# Shared helpers used by the tool modules
# ---------------------------------------------------------------------------


def truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    """Tool results are context. Cut them loudly rather than silently."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[...truncated, {len(text) - limit} more chars]"


def guard(name: str) -> ExecutionContext:
    """Assert the active agent may call `name`, and count the call."""
    ctx = active()
    if ctx.allowed_tools and name not in ctx.allowed_tools:
        raise ToolDenied(
            f"{ctx.agent} is not permitted to call '{name}'. "
            f"Permitted: {', '.join(sorted(ctx.allowed_tools))}."
        )
    ctx.tool_calls += 1
    return ctx


def approve(ctx: ExecutionContext, action: str, detail: str) -> bool:
    """Human-in-the-loop gate for irreversible or outward-facing actions.

    Declining returns a normal tool result so the model adapts instead of the
    loop crashing.
    """
    if ctx.auto_approve:
        return True
    print(f"\n  \033[33m{action}\033[0m {detail}")
    try:
        return input("  allow? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def fail(e: Exception) -> str:
    """Tools return errors as strings so the agent loop survives them.

    A raised exception kills the loop; a string lets the model read what went
    wrong and try something else. (patterns.md, agent-scaffold, HIGH)
    """
    return f"ERROR ({type(e).__name__}): {e}"
