"""The Orchestrator: decides who works, in what order, and what to do when
something fails.

It is not a specialist and has no tools of its own. Its job is selection,
sequencing, and judgement about failure -- which is precisely the work that
should not be delegated to the agent that just failed.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import agents as roster
from .auth import UsageLimitReached
from .context import ContextBuilder
from .handoff import AgentResult, Priority, Severity, Status
from .ledger import Ledger
from .model_planner import ModelPlanner
from .state import ProjectState
from .taskgraph import Task, TaskGraph
from .token_optimizer import (
    Budget, ContextItem, TokenOptimizer, budget_for, key_priority,
)
from .token_optimizer import Priority as CtxPriority

from .glyphs import BLUE, BOLD, G, GREEN, GREY, RED, RESET, YELLOW

# ---------------------------------------------------------------------------
# Specialist selection lives in planner.py
#
# It used to live here, as one regex table and one function. It grew a second
# stage -- a Claude capability planner -- and a merge policy with guardrails,
# which is more than an orchestrator should be carrying. These names are
# re-exported because the CLI and the test suite import them from here.
# ---------------------------------------------------------------------------

from .planner import (  # noqa: E402,F401
    CORE, IMPLIES, SIGNALS, CapabilityAnalysis, Choice, Selection, analyse,
    detect, mandatory_for, select_by_rules, select_specialists,
)

# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------

OBJECTIVES: dict[str, str] = {
    "requirements_analyst": "Turn the problem statement into numbered functional and non-functional requirements, constraints, acceptance criteria, judging criteria and submission requirements.",
    "market_researcher": "Size and characterise the market this product enters, with sourced figures.",
    "competitor_researcher": "Identify who already solves this, compare them, and find the exploitable weakness.",
    "technical_researcher": "Recommend the shortest credible technical path, naming what you rejected and why.",
    "user_researcher": "Define the primary user, their current workflow, and the journey we must support.",
    "product_manager": "Decide the MVP and produce a MoSCoW breakdown, including an explicit DO NOT BUILD list.",
    "strategist": "Define positioning and differentiation, and map every judging criterion to the evidence we will offer.",
    "architect": "Design the minimum architecture that meets the MUST HAVE scope and demos reliably.",
    "ux_designer": "Specify the user journey, IA, and the onboarding, empty and error states.",
    "ui_designer": "Specify the visual system with concrete values a frontend engineer can implement directly.",
    "brand_designer": "Name the product and write the tagline and positioning line. Timebox this.",
    "backend_engineer": "Implement the API and business logic for the MUST HAVE scope, and run it.",
    "frontend_engineer": "Build the demo-ready interface against the UX and UI specs.",
    "ml_engineer": "Build the model pipeline and evaluate it honestly against the metric being scored.",
    "ai_engineer": "Build the LLM integration: prompts, tools, and the agent loop.",
    "database_engineer": "Create the schema, indexes and seed data that make the demo look alive.",
    "devops_engineer": "Make the project start from a clean clone with one command, with no secrets committed.",
    "developer": "Wire the components together and make the end-to-end path run.",
    "tester": "Test the product for real and report PASS/FAIL/BLOCKED per case, prioritising the demo path.",
    "code_reviewer": "Review the code for correctness and unnecessary complexity; rank findings by severity.",
    "security_reviewer": "Scan for secrets and insecure patterns, then triage what the scanners return.",
    "requirements_auditor": "Check every requirement against evidence on disk and mark it satisfied, partial or missing.",
    "technical_writer": "Document what was actually built: README, technical docs, verified setup steps.",
    "pitch_strategist": "Map each judging criterion to real evidence and structure the narrative.",
    "presentation_builder": "Write the deck and render it to a real .pptx. Invent nothing.",
    "demo_engineer": "Design and rehearse the shortest demo that proves the core value, with a fallback.",
    "final_auditor": "Audit the whole submission as a hostile judge and classify every issue.",
    "submission_manager": "Verify every submission requirement against disk and package the deliverable.",
}

# (id, agent, deps, priority, impact, effort, phase, optional)
BLUEPRINT: list[tuple] = [
    ("requirements", "requirements_analyst", (), "CRITICAL", 5, 2, "intake", False),
    ("market", "market_researcher", (), "MEDIUM", 3, 3, "research", True),
    ("competition", "competitor_researcher", (), "MEDIUM", 3, 3, "research", True),
    ("tech_research", "technical_researcher", (), "HIGH", 4, 3, "research", False),
    ("user_research", "user_researcher", (), "HIGH", 4, 2, "research", False),
    ("product_plan", "product_manager", ("requirements",), "CRITICAL", 5, 2, "plan", False),
    ("strategy", "strategist", ("requirements",), "HIGH", 4, 2, "plan", False),
    ("architecture", "architect", ("requirements", "product_plan"), "CRITICAL", 5, 3, "plan", False),
    ("ux", "ux_designer", ("product_plan",), "HIGH", 4, 2, "design", False),
    ("ui", "ui_designer", ("ux",), "MEDIUM", 3, 2, "design", False),
    ("brand", "brand_designer", ("strategy",), "LOW", 2, 1, "design", True),
    ("backend", "backend_engineer", ("architecture",), "CRITICAL", 5, 4, "build", False),
    ("database", "database_engineer", ("architecture",), "HIGH", 3, 3, "build", False),
    ("ml", "ml_engineer", ("architecture",), "CRITICAL", 5, 5, "build", False),
    ("ai", "ai_engineer", ("architecture",), "CRITICAL", 5, 4, "build", False),
    ("frontend", "frontend_engineer", ("architecture",), "HIGH", 4, 4, "build", False),
    ("devops", "devops_engineer", ("architecture",), "MEDIUM", 3, 2, "build", False),
    ("integrate", "developer", ("architecture",), "HIGH", 4, 3, "build", True),
    ("test", "tester", ("architecture",), "CRITICAL", 5, 3, "validate", False),
    ("code_review", "code_reviewer", ("architecture",), "MEDIUM", 3, 2, "validate", True),
    ("security", "security_reviewer", ("architecture",), "HIGH", 4, 2, "validate", False),
    ("docs", "technical_writer", ("architecture",), "HIGH", 4, 2, "deliver", False),
    ("demo", "demo_engineer", ("test",), "CRITICAL", 5, 2, "deliver", False),
    ("pitch", "pitch_strategist", ("strategy",), "HIGH", 4, 2, "deliver", False),
    ("slides", "presentation_builder", ("pitch",), "HIGH", 4, 2, "deliver", False),
    ("req_audit", "requirements_auditor", ("test",), "HIGH", 4, 2, "deliver", False),
    ("final_audit", "final_auditor", ("req_audit", "demo", "docs", "slides"), "CRITICAL", 5, 2, "deliver", False),
    ("submission", "submission_manager", ("final_audit",), "CRITICAL", 5, 2, "deliver", False),
]


def build_plan(selection: Selection) -> TaskGraph:
    """Instantiate the blueprint for exactly the selected specialists.

    Dependencies on a dropped task are rewired to that task's own dependencies,
    so removing the ML engineer does not orphan everything downstream of it.
    """
    chosen_ids = {tid for tid, agent, *_ in BLUEPRINT if agent in selection.chosen}
    raw = {tid: deps for tid, _a, deps, *_ in BLUEPRINT}

    def rewire(deps: tuple[str, ...], seen: frozenset = frozenset()) -> tuple[str, ...]:
        out: list[str] = []
        for d in deps:
            if d in chosen_ids:
                out.append(d)
            elif d not in seen:
                out.extend(rewire(raw.get(d, ()), seen | {d}))
        return tuple(dict.fromkeys(out))

    g = TaskGraph()
    for tid, agent, deps, prio, impact, effort, phase, optional in BLUEPRINT:
        if agent not in selection.chosen:
            continue
        g.add(Task(
            id=tid, agent=agent, objective=OBJECTIVES.get(agent, f"Do the {agent} work."),
            depends_on=rewire(deps), priority=Priority(prio),
            impact=impact, effort=effort, phase=phase, optional=optional,
        ))
    g.validate()
    return g


# ---------------------------------------------------------------------------
# The Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    MAX_ATTEMPTS = 2

    def __init__(
        self,
        state: ProjectState,
        backend,
        *,
        parallel: int = 3,
        auto_approve: bool = True,
        verbose: bool = True,
        dry_run: bool = False,
        cache: bool = True,
        model: str = "",
        optimize: bool = True,
        intelligent: bool = True,
    ) -> None:
        self.state = state
        self.backend = backend
        self.parallel = max(1, parallel)
        self.auto_approve = auto_approve
        self.verbose = verbose
        self.dry_run = dry_run
        self.ctxb = ContextBuilder(state.root)
        # Completed work survives a crash, a usage limit, or a night's sleep.
        self.ledger = Ledger.load(state.root, enabled=cache)
        # Set when a usage window closes mid-run; `run` stops on it.
        self.limit_hit: UsageLimitReached | None = None

        self.optimize = optimize
        self.intelligent = intelligent
        self.optimizer = TokenOptimizer(state.root, cache=cache)
        self.optimizer.metrics.merge(state.token_metrics)
        # Model decisions are restored, never recomputed: they are part of the
        # ledger's fingerprint, so re-deciding on resume would invalidate every
        # completed task and re-run the whole hackathon.
        self.models = ModelPlanner.from_dict(
            state.model_decisions,
            override=model,
            project_complexity=state.selection.analysis.complexity,
        )

    # -- planning --------------------------------------------------------

    def plan(self, *, depth: str = "full", intelligent: bool | None = None) -> Selection:
        """The unified planner: analyse, select, build the graph, assign models.

        The order matters. Specialist selection has to happen before the task
        graph exists (it decides which tasks there are), and model planning has
        to happen after (it reads each task's own priority and effort).
        """
        use_planner = self.intelligent if intelligent is None else intelligent
        sel = select_specialists(
            self.state.full_brief, depth=depth,
            backend=self.backend if use_planner else None,
            intelligent=use_planner, verbose=self.verbose,
        )
        self.state.selection = sel
        self.state.graph = build_plan(sel)
        self.models.project_complexity = sel.analysis.complexity
        self.assign_models()
        self.plan_budgets()
        self.state.advance_phase()
        self.state.save()
        self._write_plan_doc(sel)
        return sel

    def assign_models(self) -> dict:
        """Decide, once, which model each task runs on."""
        caps = self.state.selection.analysis.capabilities
        for task in self.state.graph.tasks.values():
            self.models.decide(task, roster.get(task.agent), capabilities=caps)
        self.state.model_decisions = self.models.to_dict()
        return self.state.model_decisions

    def plan_budgets(self) -> dict:
        """Size the context/output/research budget for every planned task."""
        complexity = self.state.selection.analysis.complexity
        for task in self.state.graph.tasks.values():
            b = budget_for(roster.get(task.agent), task, project_complexity=complexity)
            self.state.budgets[task.id] = b.to_dict()
        return self.state.budgets

    def budget_for_task(self, task: Task) -> Budget:
        stored = self.state.budgets.get(task.id)
        if stored:
            return Budget.from_dict(stored)
        b = budget_for(roster.get(task.agent), task,
                       project_complexity=self.state.selection.analysis.complexity)
        self.state.budgets[task.id] = b.to_dict()
        return b

    # -- context ---------------------------------------------------------

    def candidate_context(self, task: Task) -> list[ContextItem]:
        """Everything this task is entitled to see, before any optimisation.

        Separated from `build_context` so the optimiser's saving can be
        measured against the same set it was given, rather than against a
        differently-assembled approximation of it.
        """
        spec = roster.get(task.agent)
        items: list[ContextItem] = []
        for key in spec.context_keys:
            body = self.ctxb.slice_for(key, limit=6000)
            if body:
                items.append(ContextItem(
                    key=key, body=body,
                    priority=key_priority(key, tuple(spec.requires)), source=key,
                ))
        upstream = self._upstream_results(task)
        if upstream:
            items.append(ContextItem(
                key="upstream", body=self.ctxb._recent(upstream),
                priority=CtxPriority.STATE, source="handoffs",
            ))
        # Only the failure that is actually blocking this task, not every note.
        failures = self._relevant_failures(task)
        if failures:
            items.append(ContextItem(
                key="failures", body=failures, priority=CtxPriority.TASK,
                source="open failures",
            ))
        return items

    def build_context(self, task: Task) -> tuple[str, Budget]:
        """Assemble only what this task needs, then optimise it.

        The unoptimised path is kept reachable (`--no-optimize`) because it is
        the baseline the optimiser's savings are measured against, and because
        a bug in prioritisation should be debuggable by turning it off.
        """
        spec = roster.get(task.agent)
        budget = self.budget_for_task(task)
        if not self.optimize:
            return self.ctxb.build(spec.context_keys, recent=self.state.history), budget

        opt = self.optimizer.optimize(self.candidate_context(task), budget)
        self.state.token_metrics = self.optimizer.metrics
        if self.verbose and opt.dropped:
            print(f"      {GREY}{opt.explain()}{RESET}")
        return opt.text, budget

    def _upstream_results(self, task: Task) -> list[AgentResult]:
        """The handoffs from *this* task's dependencies, not the run's tail.

        Two reasons, and the second is the one that bites. Relevance: the UI
        Designer does not need to hear what the Market Researcher reported, and
        sending it spends the window on noise. Stability: the last five
        handoffs of a run change on every wave, so a context built from them
        changes too -- which changes the ledger fingerprint, which means a
        resumed run re-runs work it had already paid for.
        """
        out: list[AgentResult] = []
        for dep in task.depends_on:
            up = self.state.graph.tasks.get(dep)
            if up is not None and up.result is not None:
                out.append(up.result)
        return out

    def _relevant_failures(self, task: Task) -> str:
        """What went wrong upstream of *this* task, and nothing else."""
        rows = []
        for dep in task.depends_on:
            up = self.state.graph.tasks.get(dep)
            if up and up.status is Status.FAILED and up.result:
                note = (up.result.notes[-1] if up.result.notes else up.result.summary)
                rows.append(f"- `{dep}` ({up.agent}) failed: {note[:220]}")
        if not rows:
            return ""
        return "## Upstream failures affecting this task\n\n" + "\n".join(rows)

    def _write_plan_doc(self, sel: Selection) -> None:
        lines = [
            "# Orchestration Plan", "",
            f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}", "",
            f"## Selected specialists ({len(sel.chosen)} of {len(roster.REGISTRY)})", "",
        ]
        for name in sorted(sel.chosen):
            spec = roster.get(name)
            lines.append(f"- **{spec.title}** (`{name}`) — {sel.reasons.get(name, '')}")
        lines += ["", "## Deliberately not activated", ""]
        for name in sorted(sel.skipped):
            if name in sel.chosen:
                continue
            lines.append(f"- `{name}` — {sel.skipped[name]}")
        lines += ["", "## Task graph", "", "```", self.state.graph.ascii(), "```", ""]
        p = self.state.root / "AGENT/plan.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines), encoding="utf-8")

    # -- execution -------------------------------------------------------

    def next_actions(self) -> list[Task]:
        return self.state.graph.ready(self.state.root, roster.REGISTRY)

    def run_task(self, task: Task) -> AgentResult:
        spec = roster.get(task.agent)
        specialist = roster.Specialist(spec)
        context, budget = self.build_context(task)
        tier, decision = self.models.tier_for(
            task, spec, capabilities=self.state.selection.analysis.capabilities
        )
        self.state.model_decisions = self.models.to_dict()
        if self.verbose:
            print(f"    {BOLD}{spec.title}{RESET} {GREY}({task.id}, {decision.model}, "
                  f"~{budget.context} ctx tok){RESET}")
        result = specialist.run(
            self.state.root, task.objective, context, self.backend,
            task_id=task.id, auto_approve=self.auto_approve, dry_run=self.dry_run,
            # Each failed attempt escalates the model tier, so a retry is a
            # different bet rather than the same one made twice.
            attempt=task.attempts, ledger=self.ledger, tier=tier, budget=budget,
        )
        return result

    def run_wave(self, tasks: list[Task]) -> list[tuple[Task, AgentResult]]:
        """Run everything currently runnable, in parallel where it is safe.

        Tasks whose write scopes overlap are run sequentially -- two agents
        appending to the same ledger concurrently is a lost-update bug, and a
        hackathon is the wrong place to debug one.
        """
        if len(tasks) == 1 or self.parallel == 1:
            return [(t, self.run_task(t)) for t in tasks]

        groups: list[list[Task]] = []
        claimed: list[set[str]] = []
        for t in tasks:
            scope = set(roster.get(t.agent).write_paths)
            for i, c in enumerate(claimed):
                if not (c & scope):
                    groups[i].append(t)
                    claimed[i] |= scope
                    break
            else:
                groups.append([t])
                claimed.append(scope)

        # groups[i] holds tasks that must not run together; run one from each
        # group concurrently, repeatedly. Results are paired with their task
        # here rather than zipped by position later -- grouping reorders the
        # list, and positional pairing silently files each agent's result under
        # a neighbour's task.
        results: list[tuple[Task, AgentResult]] = []
        while any(groups):
            slice_ = [g.pop(0) for g in groups if g]
            groups = [g for g in groups if g]
            if len(slice_) == 1:
                results.append((slice_[0], self.run_task(slice_[0])))
            else:
                with ThreadPoolExecutor(max_workers=min(self.parallel, len(slice_))) as ex:
                    results.extend(zip(slice_, ex.map(self.run_task, slice_)))
        return results

    def step(self) -> list[AgentResult]:
        """Run one wave and fold the outcomes back into the graph."""
        ready = self.next_actions()
        if not ready:
            return []
        ready = ready[: max(self.parallel, 1) * 2]
        pairs = self.run_wave(ready)

        for task, result in pairs:
            self.state.graph.record(task, result)
            self.state.record(result)
            self._report(task, result)
            if result.status is Status.FAILED:
                self.self_correct(task, result)
            self._absorb_next_tasks(task, result)
            self.consider_replan(task, result)

        self.state.advance_phase()
        self._sync_metrics()
        self.state.save()
        return [r for _t, r in pairs]

    def _sync_metrics(self) -> None:
        """Fold the optimiser's and ledger's counters into persisted state."""
        self.optimizer.metrics.cache_hits = max(
            self.optimizer.metrics.cache_hits, int(getattr(self.ledger, "hits", 0) or 0)
        )
        self.optimizer.metrics.cache_misses = max(
            self.optimizer.metrics.cache_misses,
            int(getattr(self.ledger, "misses", 0) or 0),
        )
        self.state.token_metrics = self.optimizer.metrics
        self.state.model_decisions = self.models.to_dict()

    def run(self, *, max_waves: int = 25) -> ProjectState:
        for wave in range(1, max_waves + 1):
            ready = self.next_actions()
            if not ready:
                break
            if self.verbose:
                names = ", ".join(t.id for t in ready[: self.parallel * 2])
                print(f"\n  {BLUE}wave {wave}{RESET} {GREY}({names}){RESET}")
            try:
                if not self.step():
                    break
            except UsageLimitReached as e:
                # Stop, do not retry, do not reach for a paid credential. Any
                # task that finished before the window closed is already in the
                # ledger, so resuming replays it without a model call.
                self.limit_hit = e
                self.state.notes.append(f"run halted: {e}")
                if self.verbose:
                    print(f"\n  {RED}usage limit{RESET} {e}")
                break
        self.state.advance_phase()
        self._sync_metrics()
        self.state.save()
        if self.verbose and self.ledger.enabled:
            print(f"  {GREY}{self.ledger.stats()}{RESET}")
            m = self.state.token_metrics
            print(f"  {GREY}context: ~{m.estimated_input_tokens:,} tok sent, "
                  f"~{m.context_tokens_removed:,} removed "
                  f"(compression {m.context_compression_ratio}){RESET}")
        return self.state

    # -- failure handling ------------------------------------------------

    def self_correct(self, task: Task, result: AgentResult) -> None:
        """React to a failed task instead of continuing past it.

        Identify what went wrong, say what it costs, propose a correction,
        write it to the decision log, and re-queue the work with the failure
        made explicit so the retry is not a blind repeat.
        """
        reason = result.notes[-1] if result.notes else (result.summary or "no reason given")
        consequence = self._downstream_names(task.id)
        line = (
            f"{task.agent} failed: {reason}. "
            f"Blocks: {', '.join(consequence) if consequence else 'nothing downstream'}."
        )
        self.state.notes.append(line)
        if self.verbose:
            print(f"    {RED}self-correction{RESET} {line}")

        self._log_decision(
            what=f"Retry {task.id} after failure",
            why=f"{reason} | downstream affected: {', '.join(consequence) or 'none'}",
        )

        if task.attempts < self.MAX_ATTEMPTS:
            # A failure is the evidence that justifies spending more. This is
            # the only place a model changes mid-run, and it changes between
            # attempts rather than inside one -- switching models partway
            # through a task throws away everything the first one worked out.
            # escalate() mutates the stored decision in place, so there is no
            # "before" object to hold on to -- the escalation record is what
            # carries the previous model.
            after = self.models.escalate(task.id, reason[:160])
            if after is not None and after.escalations:
                last = after.escalations[-1]
                line_up = (f"model escalation: {last['from']} -> {last['to']} "
                           f"(reason: {last['reason']})")
                self.state.notes.append(line_up)
                if self.verbose:
                    print(f"    {YELLOW}{line_up}{RESET}")
            self.state.model_decisions = self.models.to_dict()

            task.status = None
            task.objective = (
                f"{task.objective}\n\n"
                f"## Previous attempt failed\n\n"
                f"Your last attempt was rejected: {reason}\n"
                f"Fix precisely that. Produce every required artifact in full before "
                f"calling submit_handoff."
            )
            if self.verbose:
                print(f"    {YELLOW}re-queued{RESET} {task.id} (attempt {task.attempts + 1})")
        elif self.verbose:
            print(f"    {RED}giving up on {task.id}{RESET} after {task.attempts} attempts")

    def _downstream_names(self, tid: str) -> list[str]:
        return [t.id for t in self.state.graph.tasks.values() if tid in t.depends_on]

    def _absorb_next_tasks(self, task: Task, result: AgentResult) -> None:
        """Schedule fix work an agent proposed -- but only for real failures.

        The Tester cannot edit src/, so a FAIL it reports has to become a
        Developer task or it never gets fixed. We accept proposals that name a
        selected specialist and carry real urgency, and ignore wishlists.
        """
        if "developer" not in {t.agent for t in self.state.graph.tasks.values()}:
            return
        urgent = [
            nt for nt in result.next_tasks
            if nt.priority in (Priority.CRITICAL, Priority.HIGH)
            and nt.agent in roster.REGISTRY
        ]
        for i, nt in enumerate(urgent[:2]):
            tid = f"{task.id}_fix{i + 1}"
            if tid in self.state.graph.tasks:
                continue
            try:
                self.state.graph.add(Task(
                    id=tid, agent=nt.agent,
                    objective=f"{nt.objective}\n\nRaised by {task.agent}: {nt.reason}",
                    depends_on=(task.id,), priority=nt.priority,
                    impact=4, effort=2, phase="validate",
                ))
            except ValueError:
                continue
            if self.verbose:
                print(f"    {YELLOW}scheduled{RESET} {tid} -> {nt.agent}")

    # -- replanning ------------------------------------------------------

    #: Phrases in a handoff that mean "the capability map was wrong". Kept
    #: narrow on purpose: a specialist musing about what would be nice is not
    #: a reason to add a whole new task to a hackathon on a deadline.
    REPLAN_SIGNALS: dict[str, tuple[str, ...]] = {
        "ml": ("custom model", "needs training", "train a classifier",
               "fine-tun", "custom classification"),
        "database": ("needs a database", "requires persistence", "needs a schema"),
        "frontend": ("needs an interface", "needs a ui", "needs a frontend"),
        "devops": ("needs deployment", "must be deployable", "needs a container"),
        "security": ("handles credentials", "stores personal data", "security review needed"),
    }

    def consider_replan(self, task: Task, result: AgentResult) -> list[str]:
        """React to new information by extending the plan, not restarting it.

        A specialist that discovers the project actually needs a capability
        nobody staffed is the single most valuable signal in the run. The
        response is to add exactly the missing specialists and exactly the
        tasks that depend on them -- everything already completed stays
        completed, and the ledger replays it if anything re-touches it.
        """
        if result.status is not Status.COMPLETED:
            return []
        blob = " ".join(
            [result.summary or ""]
            + [f.summary for f in result.findings]
            + [d.what for d in result.decisions]
            + list(result.risks)
        ).lower()

        discovered: list[str] = []
        for cap, phrases in self.REPLAN_SIGNALS.items():
            if self.state.selection.analysis.capabilities.get(cap):
                continue
            if cap in self.state.selection.analysis.excluded:
                continue  # the brief ruled it out; an agent does not overrule it
            if any(p in blob for p in phrases):
                discovered.append(cap)
        if not discovered:
            return []
        return self.replan(discovered, because=f"{task.agent} ({task.id})")

    def replan(self, capabilities: list[str], *, because: str = "") -> list[str]:
        """Add the specialists a newly discovered capability needs.

        Returns the task ids added. Completed tasks are never touched; the new
        tasks are wired into the existing graph, and only what depends on them
        has to wait.
        """
        added_tasks: list[str] = []
        sel = self.state.selection
        for cap in capabilities:
            sel.analysis.capabilities[cap] = True
            for agent in IMPLIES.get(cap, ()):
                if agent in sel.chosen:
                    continue
                sel.pick(agent, f"replan: '{cap}' discovered by {because}",
                         priority="high", source="replan")

        # Instantiate any blueprint task whose agent is now selected. Rewiring
        # uses the *current* graph, so a dependency that already completed
        # stays completed and the new task simply becomes ready.
        existing = set(self.state.graph.tasks)
        chosen_ids = {tid for tid, agent, *_ in BLUEPRINT if agent in sel.chosen}
        raw = {tid: deps for tid, _a, deps, *_ in BLUEPRINT}

        def rewire(deps: tuple[str, ...], seen: frozenset = frozenset()) -> tuple[str, ...]:
            out: list[str] = []
            for d in deps:
                if d in chosen_ids:
                    out.append(d)
                elif d not in seen:
                    out.extend(rewire(raw.get(d, ()), seen | {d}))
            return tuple(dict.fromkeys(out))

        for tid, agent, deps, prio, impact, effort, phase, optional in BLUEPRINT:
            if agent not in sel.chosen or tid in existing:
                continue
            wired = tuple(d for d in rewire(deps) if d in self.state.graph.tasks)
            try:
                self.state.graph.add(Task(
                    id=tid, agent=agent,
                    objective=OBJECTIVES.get(agent, f"Do the {agent} work."),
                    depends_on=wired, priority=Priority(prio), impact=impact,
                    effort=effort, phase=phase, optional=optional,
                ))
            except ValueError:
                continue
            added_tasks.append(tid)

        if not added_tasks:
            return []

        self.state.graph.validate()
        for tid in added_tasks:
            task = self.state.graph.tasks[tid]
            self.models.decide(task, roster.get(task.agent),
                               capabilities=sel.analysis.capabilities)
            self.state.budgets[tid] = budget_for(
                roster.get(task.agent), task,
                project_complexity=sel.analysis.complexity,
            ).to_dict()

        record = {
            "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "capabilities": capabilities, "because": because,
            "tasks_added": added_tasks,
        }
        self.state.replans.append(record)
        self.state.model_decisions = self.models.to_dict()
        self._log_decision(
            what=f"Replanned: added {', '.join(added_tasks)}",
            why=f"{because} showed the project needs {', '.join(capabilities)}. "
                f"Completed work was left untouched.",
        )
        if self.verbose:
            print(f"    {YELLOW}replan{RESET} +{', '.join(added_tasks)} "
                  f"{GREY}({', '.join(capabilities)} discovered){RESET}")
        self.state.save()
        return added_tasks

    def _log_decision(self, what: str, why: str) -> None:
        p = self.state.root / "AGENT/decision_log.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("# Decision Log\n", encoding="utf-8")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n## {what}\n\n- **Why:** {why}\n- **By:** orchestrator on "
                f"{datetime.now(timezone.utc).date().isoformat()}\n"
            )

    # -- reporting -------------------------------------------------------

    def _report(self, task: Task, result: AgentResult) -> None:
        if not self.verbose:
            return
        mark = {
            Status.COMPLETED: f"{GREEN}{G['ok']}{RESET}",
            Status.FAILED: f"{RED}{G['fail']}{RESET}",
            Status.BLOCKED: f"{YELLOW}{G['unreachable']}{RESET}",
            Status.SKIPPED: f"{GREY}{G['skip']}{RESET}",
            Status.NEEDS_HUMAN: f"{YELLOW}{G['human']}{RESET}",
        }.get(result.status, G["bullet"])
        arts = ", ".join(a.path for a in result.artifacts[:3])
        print(f"    {mark} {task.id:14} {GREY}{arts or 'no artifacts'}{RESET}")
        for f in result.findings:
            if f.severity in (Severity.CRITICAL, Severity.HIGH):
                print(f"      {RED if f.severity is Severity.CRITICAL else YELLOW}"
                      f"{f.severity.value}{RESET} {f.summary[:110]}")
        if result.status is not Status.COMPLETED and result.notes:
            print(f"      {GREY}{result.notes[-1][:160]}{RESET}")

    # -- human-in-the-loop ----------------------------------------------

    def gates(self) -> list[str]:
        """Things a human should decide before this is considered finished."""
        out = []
        for c in self.state.open_criticals():
            out.append(f"CRITICAL finding open: {c.summary}")
        for t in self.state.graph.tasks.values():
            if t.status is Status.NEEDS_HUMAN and t.result:
                out.append(f"{t.agent} needs input: {t.result.summary[:160]}")
        sub = self.state.graph.tasks.get("submission")
        if sub and sub.status is Status.COMPLETED:
            out.append("Submission packaged — confirm before sending it anywhere.")
        return out

    def next_best_action(self) -> str:
        ready = self.next_actions()
        if ready:
            t = ready[0]
            return f"Run `{t.id}` ({roster.get(t.agent).title}) — {t.priority.value}"
        blocked = self.state.graph.blocked()
        if blocked:
            return f"Unblock: {blocked[0].id} is unreachable after an upstream failure"
        failed = [t for t in self.state.graph.tasks.values() if t.status is Status.FAILED]
        if failed:
            return f"Resolve failure in `{failed[0].id}` ({failed[0].agent})"
        if self.state.graph.pending():
            return "Waiting on dependencies — run `hackathon status` for the graph"
        crit = self.state.open_criticals()
        if crit:
            return f"Resolve {len(crit)} CRITICAL audit finding(s) before submitting"
        return "Everything planned is done — review FINAL/final_audit.md and submit"
