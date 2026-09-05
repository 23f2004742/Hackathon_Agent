"""The specialist roster.

Importing this validates every spec: unknown tools, artifacts declared outside
a write scope, and produces-without-write-paths all raise at import time rather
than failing halfway through a hackathon.
"""

from __future__ import annotations

from .base import AgentSpec, Specialist
from .communication.specs import SPECS as COMMUNICATION_SPECS
from .delivery.specs import SPECS as DELIVERY_SPECS
from .design.specs import SPECS as DESIGN_SPECS
from .engineering.specs import SPECS as ENGINEERING_SPECS
from .product.specs import SPECS as PRODUCT_SPECS
from .research.specs import SPECS as RESEARCH_SPECS
from .validation.specs import SPECS as VALIDATION_SPECS

ALL_SPECS: list[AgentSpec] = [
    *RESEARCH_SPECS,
    *PRODUCT_SPECS,
    *ENGINEERING_SPECS,
    *DESIGN_SPECS,
    *VALIDATION_SPECS,
    *COMMUNICATION_SPECS,
    *DELIVERY_SPECS,
]

REGISTRY: dict[str, AgentSpec] = {s.name: s for s in ALL_SPECS}

if len(REGISTRY) != len(ALL_SPECS):
    seen, dupes = set(), []
    for s in ALL_SPECS:
        (dupes.append(s.name) if s.name in seen else seen.add(s.name))
    raise ValueError(f"duplicate agent names: {dupes}")

TEAMS: dict[str, list[AgentSpec]] = {}
for _s in ALL_SPECS:
    TEAMS.setdefault(_s.team, []).append(_s)


def get(name: str) -> AgentSpec:
    if name not in REGISTRY:
        raise KeyError(f"unknown specialist '{name}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[name]


def specialist(name: str) -> Specialist:
    return Specialist(get(name))


def producers() -> dict[str, str]:
    """Artifact path -> the specialist that owns producing it."""
    out: dict[str, str] = {}
    for s in ALL_SPECS:
        for p in s.produces:
            out.setdefault(p, s.name)
    return out


__all__ = [
    "ALL_SPECS", "REGISTRY", "TEAMS", "AgentSpec", "Specialist",
    "get", "specialist", "producers",
]
