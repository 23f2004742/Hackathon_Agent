"""Tool registry. Importing this package registers every tool.

Specialists never import tool modules directly -- they name tools in their spec
and the runner hands them the corresponding SDK objects via `belt()`. That
indirection is the access boundary.
"""

from __future__ import annotations

from . import (  # noqa: F401
    documents, filesystem, handoff_tool, project, research, security, shell,
)
from .base import (  # noqa: F401
    REGISTRY,
    ExecutionContext,
    ToolDenied,
    ToolSpec,
    active,
    belt,
    categories,
    truncate,
    using,
)
from .research import WEB_SEARCH_SERVER_TOOL  # noqa: F401

# Server-side tools are addressed by name in a spec's `tools` list but resolve
# to a raw dict rather than a local function.
SERVER_TOOLS: dict[str, dict] = {
    "web_search": WEB_SEARCH_SERVER_TOOL,
}


def resolve(names: list[str]) -> list:
    """Turn a spec's tool-name list into the list handed to the API."""
    out = []
    for n in names:
        if n in SERVER_TOOLS:
            out.append(SERVER_TOOLS[n])
        elif n in REGISTRY:
            out.append(REGISTRY[n].fn)
        else:
            raise KeyError(
                f"unknown tool '{n}'. Registered: {sorted(REGISTRY)}; "
                f"server-side: {sorted(SERVER_TOOLS)}"
            )
    return out


def local_names() -> set[str]:
    return set(REGISTRY)


def all_names() -> set[str]:
    return set(REGISTRY) | set(SERVER_TOOLS)


__all__ = [
    "REGISTRY", "SERVER_TOOLS", "ExecutionContext", "ToolDenied", "ToolSpec",
    "active", "belt", "categories", "resolve", "local_names", "all_names",
    "truncate", "using",
]
