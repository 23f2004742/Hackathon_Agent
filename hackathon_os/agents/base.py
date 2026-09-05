"""What a specialist *is*, and how one is run.

A specialist is not a system prompt. It is the combination of:

  tools        - the only tools it is handed (access boundary, enforced in code)
  write_paths  - the only paths it may write (enforced in the filesystem layer)
  requires     - inputs that must exist before it may start
  produces     - artifacts it must leave behind to be considered complete
  postconditions - programmatic checks on those artifacts
  context_keys - which slices of shared context it receives

Change any of those and you have a different specialist. Change only the prose
and you have the same one wearing a hat -- which is the failure mode this
module exists to prevent.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..auth import UsageLimitReached
from ..handoff import AgentResult, Artifact, Decision, Finding, NextTask, Status
from ..tools import ExecutionContext, ToolDenied, all_names, resolve, using

# ---------------------------------------------------------------------------
# Postconditions -- declarative checks run against artifacts after the agent
# stops. These are how "did it actually do the work" gets answered without
# asking the model to grade itself.
# ---------------------------------------------------------------------------


class Check(Protocol):
    def __call__(self, root: Path) -> str | None:
        """Return None if the check passes, else a one-line reason it failed."""

    def describe(self) -> str:
        """The requirement in the model's own terms, for the system prompt.

        A contract the agent only discovers by failing it costs a whole extra
        model run to learn. Stating it up front is strictly cheaper, and on a
        subscription the currency is rate-limit windows.
        """


@dataclass
class FileContains:
    """The artifact must mention each of these, case-insensitively."""

    path: str
    needles: tuple[str, ...]
    label: str = ""

    def describe(self) -> str:
        return (f"{self.path} must explicitly address each of these, using these "
                f"words: {', '.join(self.needles)}")

    def __call__(self, root: Path) -> str | None:
        p = root / self.path
        if not p.is_file():
            return f"{self.path} missing"
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        missing = [n for n in self.needles if n.lower() not in text]
        if missing:
            what = self.label or self.path
            return f"{what} does not cover: {', '.join(missing)}"
        return None


@dataclass
class ValidJson:
    """The artifact must parse as JSON, and contain these top-level keys."""

    path: str
    keys: tuple[str, ...] = ()

    def describe(self) -> str:
        keys = f" with top-level keys: {', '.join(self.keys)}" if self.keys else ""
        return f"{self.path} must be valid JSON{keys}"

    def __call__(self, root: Path) -> str | None:
        p = root / self.path
        if not p.is_file():
            return f"{self.path} missing"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return f"{self.path} is not valid JSON: {e}"
        if isinstance(data, dict):
            missing = [k for k in self.keys if k not in data]
            if missing:
                return f"{self.path} lacks keys: {', '.join(missing)}"
        return None


@dataclass
class HasHeadings:
    """The document must contain at least `minimum` markdown headings."""

    path: str
    minimum: int = 3

    def describe(self) -> str:
        return f"{self.path} must have at least {self.minimum} markdown headings"

    def __call__(self, root: Path) -> str | None:
        p = root / self.path
        if not p.is_file():
            return f"{self.path} missing"
        n = len(re.findall(r"^#{1,4} \S", p.read_text(encoding="utf-8", errors="replace"), re.M))
        if n < self.minimum:
            return f"{self.path} has {n} headings, expected at least {self.minimum}"
        return None


@dataclass
class MinWords:
    path: str
    minimum: int = 150

    def describe(self) -> str:
        return f"{self.path} must be at least {self.minimum} words of real content"

    def __call__(self, root: Path) -> str | None:
        p = root / self.path
        if not p.is_file():
            return f"{self.path} missing"
        n = len(p.read_text(encoding="utf-8", errors="replace").split())
        if n < self.minimum:
            return f"{self.path} is {n} words, expected at least {self.minimum} (looks like a stub)"
        return None


@dataclass
class Custom:
    fn: Callable[[Path], str | None]
    name: str = "custom"

    def describe(self) -> str:
        return self.name

    def __call__(self, root: Path) -> str | None:
        return self.fn(root)


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------

# Every specialist gets these two regardless of team: it must be able to see
# where it is, and it must be able to report back.
UNIVERSAL_TOOLS = ("list_files", "submit_handoff")


@dataclass
class AgentSpec:
    name: str                       # stable id, e.g. "market_researcher"
    title: str                      # human label, e.g. "Market Researcher"
    team: str                       # research | product | engineering | ...
    mission: str                    # the role-specific half of the system prompt
    tools: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    postconditions: tuple[Check, ...] = ()
    context_keys: tuple[str, ...] = ("problem", "constraints")
    # Cost control: simple roles run on a smaller model at lower effort.
    model: str = "claude-opus-5"
    effort: str = "high"
    max_tokens: int = 16_000
    min_artifact_bytes: int = 400
    # Roughly how much of the hackathon budget this role deserves.
    typical_cost: str = "medium"

    def __post_init__(self) -> None:
        merged = list(dict.fromkeys(tuple(self.tools) + UNIVERSAL_TOOLS))
        unknown = [t for t in merged if t not in all_names()]
        if unknown:
            raise ValueError(f"{self.name}: unknown tools {unknown}")
        self.tools = tuple(merged)
        # An agent that must produce artifacts needs somewhere to write them.
        if self.produces and not self.write_paths:
            raise ValueError(f"{self.name}: declares produces but no write_paths")
        for rel in self.produces:
            if not self._in_scope(rel):
                raise ValueError(
                    f"{self.name}: declares produces '{rel}' outside its write scope {self.write_paths}"
                )

    def _in_scope(self, rel: str) -> bool:
        import fnmatch
        return any(
            rel == pat or rel.startswith(pat.rstrip("/") + "/") or fnmatch.fnmatch(rel, pat)
            for pat in self.write_paths
        )

    def missing_inputs(self, root: Path) -> list[str]:
        return [r for r in self.requires if not (root / r).is_file()]

    def check_postconditions(self, root: Path) -> str | None:
        for check in self.postconditions:
            problem = check(root)
            if problem:
                return problem
        return None

    @property
    def local_tools(self) -> tuple[str, ...]:
        from ..tools import SERVER_TOOLS
        return tuple(t for t in self.tools if t not in SERVER_TOOLS)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

SYSTEM_FRAME = """You are the {title} on an autonomous hackathon team.

{mission}

## Operating rules

- You are one specialist among many. Do your part completely, then stop. Do not
  do another specialist's job -- the filesystem will reject writes outside your
  scope, and duplicated work costs the team time it does not have.
- Work from evidence in the project, not from assumption. Read your required
  inputs before producing anything.
- Before building something from scratch, call knowledge_search if you have it.
  Reusing proven prior work beats rebuilding it.
- This is a hackathon: optimise for what the judging criteria actually reward.
  Scope down aggressively. A working narrow thing beats a broken broad one.
- Never claim something works that you have not run or checked.
- Record anything you assumed rather than verified; the auditor will look.

## Your write scope

You may ONLY write to: {write_scope}
Attempting to write elsewhere returns an error. That is by design.

## Required output

You MUST create these files before finishing:
{produces}

## How your work is checked

These are run against your files on disk after you stop. They are not
suggestions, and a stub that fails them marks the whole task failed:
{contract}

## Finishing

When your artifacts exist and are complete, call submit_handoff exactly once
with your structured report, then stop. Do not call submit_handoff before the
files exist -- the runner verifies them on disk and will mark you failed."""


class Specialist:
    """Runs one AgentSpec against a project, through an LLM backend."""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    # -- prompt assembly -------------------------------------------------

    def system_prompt(self) -> str:
        spec = self.spec
        checks = [c.describe() for c in spec.postconditions if hasattr(c, "describe")]
        if spec.min_artifact_bytes:
            checks.append(
                f"every file above must be at least {spec.min_artifact_bytes} bytes "
                f"-- a placeholder or 'TODO' file is treated as not written"
            )
        return SYSTEM_FRAME.format(
            title=spec.title,
            mission=spec.mission.strip(),
            write_scope=", ".join(spec.write_paths) or "(nothing -- you are read-only)",
            produces="\n".join(f"  - {p}" for p in spec.produces) or "  (no file artifacts)",
            contract="\n".join(f"  - {c}" for c in checks) or "  (no automated checks)",
        )

    def user_prompt(self, objective: str, context: str, *, budget=None) -> str:
        parts = [f"# Task\n\n{objective.strip()}"]
        if self.spec.requires:
            parts.append(
                "# Inputs to read first\n\n"
                + "\n".join(f"- {r}" for r in self.spec.requires)
            )
        if context.strip():
            parts.append(context.strip())
        if budget is not None:
            # Telling the specialist that its context is a digest is the half
            # of the optimisation that only it can act on: an agent that thinks
            # it received whole documents will not go and read them.
            parts.append(
                "# Working within budget\n\n"
                f"- Context above is a prioritised digest, not the full documents. "
                f"Anything marked `[compressed ...]` or `[digest of ...]` is "
                f"abridged -- call read_file when you need the rest.\n"
                f"- Aim for roughly {budget.output:,} tokens of output. Do the work "
                f"completely; do not pad it.\n"
                + (f"- Spend at most about {budget.research:,} tokens on research "
                   f"before you start producing.\n" if budget.research else "")
            )
        return "\n\n".join(parts)

    # -- execution -------------------------------------------------------

    def run(
        self,
        root: Path,
        objective: str,
        context: str,
        backend,
        *,
        task_id: str = "",
        auto_approve: bool = True,
        dry_run: bool = False,
        attempt: int = 0,
        ledger=None,
        tier=None,
        budget=None,
    ) -> AgentResult:
        from ..routing import route

        spec = self.spec
        started = time.time()

        missing = spec.missing_inputs(root)
        if missing:
            r = AgentResult.blocked(spec.name, [f"{m} (required input not found)" for m in missing])
            r.task_id = task_id
            r.duration_s = time.time() - started
            return r

        # Route first: the tier is part of what identifies this unit of work, so
        # a task cached at one tier is not replayed for a run at another. The
        # orchestrator normally supplies the tier, because the model comes from
        # the model planner; `route` is the fallback for a direct caller.
        if tier is None:
            tier = route(spec, attempt)
        fp = ""
        if ledger is not None:
            from ..ledger import fingerprint
            fp = fingerprint(spec, tier, root, objective, context)
            cached = ledger.lookup(fp)
            if cached is not None:
                cached.task_id = task_id
                cached.duration_s = round(time.time() - started, 2)
                return cached

        ctx = ExecutionContext(
            root=root,
            agent=spec.name,
            task_id=task_id,
            write_paths=spec.write_paths,
            allowed_tools=frozenset(spec.tools),
            auto_approve=auto_approve,
            dry_run=dry_run,
            tier=tier,
        )

        try:
            api_tools = resolve(list(spec.tools))
        except KeyError as e:
            return AgentResult(
                status=Status.FAILED, agent=spec.name, task_id=task_id,
                summary=f"tool resolution failed: {e}",
            )

        with using(ctx):
            try:
                outcome = backend.run(
                    system=self.system_prompt(),
                    user=self.user_prompt(objective, context, budget=budget),
                    tools=api_tools,
                    spec=spec,
                    ctx=ctx,
                )
            except ToolDenied as e:
                outcome = _Outcome(error=f"access boundary hit: {e}")
            except UsageLimitReached:
                # Not a failed task. Retrying against a closed usage window is
                # pointless, and the alternative to waiting is spending money.
                raise
            except Exception as e:  # noqa: BLE001 - a crashed agent is a failed task, not a crashed run
                outcome = _Outcome(error=f"{type(e).__name__}: {e}")

        result = self._assemble(ctx, outcome, task_id)
        result.duration_s = round(time.time() - started, 2)
        result = result.validate_against(spec, root)
        if ledger is not None and fp:
            ledger.record(fp, spec, tier, result)
        return result

    def _assemble(self, ctx: ExecutionContext, outcome, task_id: str) -> AgentResult:
        spec = self.spec
        if outcome.error and ctx.handoff is None:
            return AgentResult(
                status=Status.FAILED, agent=spec.name, task_id=task_id,
                summary=f"{spec.title} did not finish: {outcome.error}",
                tool_calls=ctx.tool_calls,
                input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
                notes=list(ctx.audit),
            )
        h = ctx.handoff
        if h is None:
            # The model stopped without reporting. Its artifacts may still be on
            # disk; validate_against decides. We do not invent a summary.
            return AgentResult(
                status=Status.COMPLETED, agent=spec.name, task_id=task_id,
                summary=(outcome.text or "").strip()[:600]
                or f"{spec.title} stopped without calling submit_handoff.",
                tool_calls=ctx.tool_calls,
                input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
                notes=list(ctx.audit) + ["no submit_handoff call; result inferred from disk"],
            )
        return AgentResult(
            status=Status(h.get("status", "completed")),
            agent=spec.name,
            task_id=task_id,
            summary=h.get("summary", ""),
            findings=_build_all(Finding, h.get("findings"), "summary"),
            decisions=_build_all(Decision, h.get("decisions"), "what"),
            assumptions=[str(a) for a in h.get("assumptions", [])],
            risks=[str(r) for r in h.get("risks", [])],
            next_tasks=_build_all(NextTask, h.get("next_tasks"), "objective"),
            blocked_by=[str(b) for b in h.get("blocked_by", [])],
            tool_calls=ctx.tool_calls,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            notes=list(ctx.audit),
        )


def _build_all(cls, raw, text_field: str) -> list:
    """Build dataclasses from model-supplied JSON, tolerantly.

    Models occasionally hand back a list of strings where a list of objects was
    asked for, or add keys the dataclass does not have. Neither should fail a
    task that otherwise produced good artifacts, so we coerce rather than raise
    and drop what we cannot place.
    """
    if not raw:
        return []
    fields = cls.__dataclass_fields__
    required = [
        n for n, f in fields.items()
        if f.default is __import__("dataclasses").MISSING
        and f.default_factory is __import__("dataclasses").MISSING  # type: ignore[misc]
    ]
    out = []
    for item in raw:
        if isinstance(item, str):
            item = {text_field: item}
        if not isinstance(item, dict):
            continue
        kwargs = {k: v for k, v in item.items() if k in fields}
        for r in required:
            kwargs.setdefault(r, item.get(text_field, "") or "(unspecified)")
        try:
            out.append(cls(**kwargs))
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class _Outcome:
    text: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
