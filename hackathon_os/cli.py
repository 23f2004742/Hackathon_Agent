"""The `hackathon` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import agents as roster
from . import auth as subscription_auth
from . import github as gh
from . import model_planner
from . import packaging
from . import routing
from . import tools as toolkit
from .dashboard import render
from .handoff import Status
from .auth import NoSubscriptionAuth, UsageLimitReached
from .llm import pick_backend
from .orchestrator import Orchestrator, select_specialists
from .state import ProjectState
from .taskgraph import Task
from .token_optimizer import Budget

from .glyphs import BLUE, BOLD, G, GREEN, GREY, RED, RESET, YELLOW

# Which tasks each verb is allowed to advance.
# Note: there is no "plan" verb here -- `hackathon plan` builds the graph.
# The intake/plan-phase tasks are advanced by `hackathon scope` or `run`.
PHASE_TASKS: dict[str, tuple[str, ...]] = {
    "research": ("market", "competition", "tech_research", "user_research"),
    "scope": ("requirements", "product_plan", "strategy", "architecture"),
    "design": ("ux", "ui", "brand"),
    "build": ("backend", "database", "ml", "ai", "frontend", "devops", "integrate"),
    "test": ("test", "code_review", "security"),
    "demo": ("demo",),
    "docs": ("docs",),
    "pitch": ("pitch", "slides"),
    "audit": ("req_audit", "final_audit"),
    # Renamed from "package": `hackathon package` now builds the submission
    # package itself, which is what an operator typing that word expects. This
    # verb still runs the Submission Manager specialist.
    "submit": ("submission",),
}


def _project(args) -> ProjectState:
    return ProjectState.load(Path(args.project).resolve())


def _orch(args, state: ProjectState) -> Orchestrator:
    backend = pick_backend(args.backend, verbose=not args.quiet)
    state.backend = backend.name
    return Orchestrator(
        state, backend,
        parallel=args.parallel,
        auto_approve=not args.approve,
        verbose=not args.quiet,
        dry_run=getattr(args, "dry_run", False),
        cache=not getattr(args, "no_cache", False),
        model=getattr(args, "model", "") or "",
        optimize=not getattr(args, "no_optimize", False),
        intelligent=not getattr(args, "no_planner", False),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args) -> int:
    root = Path(args.project).resolve() / args.name if args.name else Path(args.project).resolve()

    def read(value: str | None) -> str:
        if not value:
            return ""
        p = Path(value)
        return p.read_text(encoding="utf-8") if p.is_file() else value

    problem = read(args.problem)
    if not problem.strip():
        print(f"{RED}a problem statement is required{RESET} (--problem TEXT or a file path)")
        return 1

    st = ProjectState.create(
        root, args.name or root.name,
        problem=problem,
        judging=read(args.judging),
        submission=read(args.submission),
        constraints=read(args.constraints),
    )
    print(f"{GREEN}initialised{RESET} {st.root}")
    print(f"  brief files: {', '.join(sorted(p.name for p in (st.root / 'AGENT').glob('*.md')))}")
    print(f"\nNext: hackathon plan --project {st.root}")
    return 0


def cmd_agents(args) -> int:
    if args.name:
        s = roster.get(args.name)
        print(f"{BOLD}{s.title}{RESET} ({s.name}) — {s.team}\n")
        print(s.mission.strip())
        print(f"\n{BOLD}tools{RESET}       {', '.join(sorted(s.tools))}")
        print(f"{BOLD}writes{RESET}      {', '.join(s.write_paths) or '(read-only)'}")
        print(f"{BOLD}requires{RESET}    {', '.join(s.requires) or '(nothing)'}")
        print(f"{BOLD}produces{RESET}    {', '.join(s.produces) or '(no files)'}")
        print(f"{BOLD}checks{RESET}      {len(s.postconditions)} postcondition(s)")
        print(f"{BOLD}context{RESET}     {', '.join(s.context_keys)}")
        print(f"{BOLD}model{RESET}       {s.model} (effort={s.effort})")
        return 0

    for team, specs in roster.TEAMS.items():
        print(f"\n{BOLD}{team.upper()}{RESET}")
        for s in specs:
            print(f"  {s.name:<24} {GREY}{len(s.tools)} tools, "
                  f"{len(s.produces)} artifact(s){RESET}")
    print(f"\n{len(roster.REGISTRY)} specialists. `hackathon agents <name>` for detail.")
    return 0


def cmd_tools(args) -> int:
    for cat, names in toolkit.categories().items():
        print(f"\n{BOLD}{cat}{RESET}")
        for n in names:
            spec = toolkit.REGISTRY[n]
            flags = "".join(["w" if spec.writes else "-", "a" if spec.approval else "-"])
            print(f"  {n:<22} {GREY}[{flags}] {spec.summary[:70]}{RESET}")
    print(f"\n{BOLD}server-side{RESET}")
    for n in sorted(toolkit.SERVER_TOOLS):
        print(f"  {n:<22} {GREY}[--] runs on Anthropic's servers{RESET}")
    print(f"\n{len(toolkit.REGISTRY)} local tools. Flags: w=writes, a=needs approval.")
    return 0


def cmd_plan(args) -> int:
    state = _project(args)
    orch = _orch(args, state)
    sel = orch.plan(depth=args.depth)
    print(render_plan(state, orch, sel, verbose_skips=args.verbose_skips))
    print(f"\nplan written to AGENT/plan.md — run: hackathon run --project {state.root}")
    return 0


def render_plan(state: ProjectState, orch: Orchestrator, sel, *,
                verbose_skips: bool = False) -> str:
    """The `plan` report: analysis, staffing, waves, models, token plan.

    This is the inspectability requirement made concrete -- every decision the
    system just made about *how* it will spend a hackathon, on one screen,
    before a single specialist runs.
    """
    a = sel.analysis
    out: list[str] = ["", f"{BOLD}PROJECT ANALYSIS{RESET}", "-" * 16]
    out.append(f"  Type          {' + '.join(a.project_type)}")
    out.append(f"  Complexity    {a.complexity}/5")
    out.append(f"  Capabilities  {', '.join(a.present) or 'none detected'}")
    if a.excluded:
        out.append(f"  Ruled out     {', '.join(sorted(a.excluded))} "
                   f"{GREY}(the brief says no){RESET}")
    for cap, why in a.gaps.items():
        out.append(f"  {YELLOW}Gap{RESET}           {cap}: {why}")
    out.append(f"  Selection     {'rules + Claude capability planner' if sel.planner_used else 'rules only'}")
    if sel.planner_note:
        out.append(f"  Planner note  {GREY}{sel.planner_note[:150]}{RESET}")

    out += ["", f"{BOLD}SELECTED SPECIALISTS ({len(sel.chosen)} of {len(roster.REGISTRY)}){RESET}",
            "-" * 20]
    models = orch.models.decisions
    by_agent = {d.agent: d for d in models.values()}
    for name in sorted(sel.chosen):
        c = sel.choices.get(name)
        prio = (c.priority if c else "medium").upper()
        model = by_agent[name].model if name in by_agent else "-"
        out.append(f"  {GREEN}{G['ok']}{RESET} {name:<24} {GREY}{prio:<9}{model:<8}"
                   f"{(c.reason if c else '')[:70]}{RESET}")

    dropped = {k: v for k, v in sel.skipped.items() if k not in sel.chosen}
    out += ["", f"{BOLD}SKIPPED ({len(dropped)}){RESET}", "-" * 7]
    for name, why in sorted(dropped.items())[: 40 if verbose_skips else 10]:
        out.append(f"  {GREY}{G['pending']} {name:<24} {why[:80]}{RESET}")
    if not verbose_skips and len(dropped) > 10:
        out.append(f"  {GREY}... {len(dropped) - 10} more (--verbose-skips){RESET}")

    out += ["", f"{BOLD}EXECUTION WAVES{RESET}", "-" * 15]
    for i, wave in enumerate(waves(state.graph), 1):
        out.append(f"  Wave {i}  {GREY}{', '.join(t.id for t in wave)}{RESET}")

    out += ["", f"{BOLD}MODEL PLAN{RESET}", "-" * 10]
    counts = orch.models.summary()
    out.append("  " + "   ".join(f"{k}: {v} task(s)" for k, v in sorted(counts.items())))
    shown = 0
    for tid in sorted(models):
        d = models[tid]
        if d.model == model_planner.default_alias() and shown >= 3:
            continue
        out.append(f"    {d.task:<16} {d.model:<8} {GREY}{d.reason[:74]} "
                   f"(confidence {d.confidence:.2f}){RESET}")
        shown += 1

    out += ["", f"{BOLD}TOKEN PLAN{RESET}", "-" * 10]
    ctx = sum(Budget.from_dict(b).context for b in state.budgets.values())
    outb = sum(Budget.from_dict(b).output for b in state.budgets.values())
    res = sum(Budget.from_dict(b).research for b in state.budgets.values())
    out.append(f"  Estimated context   ~{ctx:,} tokens across {len(state.budgets)} tasks")
    out.append(f"  Estimated output    ~{outb:,} tokens")
    out.append(f"  Research budget     ~{res:,} tokens")
    out.append(f"  Strategy            prioritise -> deduplicate -> compress -> "
               f"drop lowest band")
    out.append(f"  Cache opportunities {len(orch.optimizer.cache.entries)} stored "
               f"digest(s), {len(orch.ledger.entries)} replayable task(s)")

    out += ["", f"{BOLD}TASK GRAPH{RESET}", "-" * 10, state.graph.ascii()]
    return "\n".join(out)


def waves(graph) -> list[list]:
    """Group tasks into the waves they would run in, ignoring current status."""
    remaining = dict(graph.tasks)
    done: set[str] = set()
    out: list[list] = []
    while remaining:
        layer = [t for t in remaining.values() if all(d in done for d in t.depends_on)]
        if not layer:
            break
        layer.sort(key=lambda t: t.sort_key)
        out.append(layer)
        for t in layer:
            done.add(t.id)
            remaining.pop(t.id)
    return out


def cmd_status(args) -> int:
    state = _project(args)
    try:
        orch = _orch(args, state)
        nba, gates = orch.next_best_action(), orch.gates()
    except Exception:  # noqa: BLE001 - status must work without a backend
        nba, gates = "", []
    print(render(state, nba, gates))
    return 0


def cmd_tasks(args) -> int:
    state = _project(args)
    g = state.graph
    if not g.tasks:
        print("no plan yet — run: hackathon plan")
        return 1
    ready = {t.id for t in g.ready(state.root, roster.REGISTRY)}
    print(f"{'ID':<18}{'AGENT':<24}{'PRIO':<10}{'STATUS':<12}DEPENDS ON")
    for t in sorted(g.tasks.values(), key=lambda x: x.sort_key):
        status = t.status.value if t.status else ("ready" if t.id in ready else "waiting")
        colour = {"completed": GREEN, "failed": RED, "blocked": YELLOW, "ready": BLUE}.get(status, GREY)
        print(f"{t.id:<18}{t.agent:<24}{t.priority.value:<10}"
              f"{colour}{status:<12}{RESET}{', '.join(t.depends_on) or '-'}")
    c = g.counts()
    print(f"\n{c['completed']} completed, {c['pending']} pending, "
          f"{c['failed']} failed, {c['blocked']} blocked")
    return 0


def cmd_run(args) -> int:
    state = _project(args)
    if not state.graph.tasks:
        print(f"{GREY}no plan yet — planning first{RESET}")
        _orch(args, state).plan(depth=args.depth)
        state = _project(args)
    orch = _orch(args, state)

    if args.task:
        task = state.graph.tasks.get(args.task)
        if not task:
            print(f"{RED}no such task:{RESET} {args.task}")
            return 1
        result = orch.run_task(task)
        state.graph.record(task, result)
        state.record(result)
        orch._report(task, result)
        state.save()
        return 0 if result.status is Status.COMPLETED else 1

    if args.once:
        results = orch.step()
        if not results:
            print(f"{GREY}nothing runnable{RESET} — {orch.next_best_action()}")
        return 0

    orch.run(max_waves=args.max_waves)
    print()
    print(render(state, orch.next_best_action(), orch.gates()))
    return 0


def cmd_phase(args, phase: str) -> int:
    """Run only the tasks belonging to one phase verb."""
    state = _project(args)
    if not state.graph.tasks:
        print("no plan yet — run: hackathon plan")
        return 1
    orch = _orch(args, state)
    wanted = set(PHASE_TASKS[phase])
    ran = 0
    for _ in range(12):
        ready = [t for t in orch.next_actions() if t.id in wanted]
        if not ready:
            break
        for task in ready:
            result = orch.run_task(task)
            state.graph.record(task, result)
            state.record(result)
            orch._report(task, result)
            if result.status is Status.FAILED:
                orch.self_correct(task, result)
            ran += 1
        state.advance_phase()
        state.save()
    if not ran:
        pending = [t.id for t in state.graph.pending() if t.id in wanted]
        print(f"{GREY}nothing runnable for '{phase}'{RESET}"
              + (f" — still waiting on dependencies for: {', '.join(pending)}" if pending else ""))
    return 0


def cmd_resume(args) -> int:
    state = _project(args)
    print(f"{BOLD}{state.name}{RESET} — phase {state.phase}, "
          f"{state.graph.counts()['completed']}/{len(state.graph.tasks)} done")
    for t in state.graph.tasks.values():
        if t.status is Status.FAILED and t.attempts < Orchestrator.MAX_ATTEMPTS:
            t.status = None
            print(f"  {YELLOW}re-queued{RESET} {t.id}")
    state.save()
    orch = _orch(args, state)
    orch.run(max_waves=args.max_waves)
    print()
    print(render(state, orch.next_best_action(), orch.gates()))
    return 0


def cmd_select(args) -> int:
    """Show which specialists a brief would activate, without creating a project."""
    src = Path(args.problem)
    if src.is_dir():
        text = "\n\n".join(f.read_text(encoding="utf-8") for f in sorted(src.glob("*.md")))
    elif src.is_file():
        text = src.read_text(encoding="utf-8")
    else:
        text = args.problem

    backend = None
    if args.planner:
        backend = pick_backend(args.backend, verbose=not args.quiet)
    sel = select_specialists(text, depth=args.depth, backend=backend,
                             intelligent=bool(backend))
    a = sel.analysis
    print(f"\n{BOLD}Analysis{RESET}  type {' + '.join(a.project_type)}, "
          f"complexity {a.complexity}/5")
    print(f"          capabilities: {', '.join(a.present) or 'none'}")
    if a.excluded:
        print(f"          ruled out: {', '.join(sorted(a.excluded))}")
    print(f"\n{BOLD}Would activate {len(sel.chosen)}{RESET}")
    for n in sorted(sel.chosen):
        print(f"  {GREEN}+{RESET} {n:<26} {GREY}{sel.reasons[n]}{RESET}")
    print(f"\n{BOLD}Would skip{RESET}")
    for n, why in sorted(sel.skipped.items()):
        if n not in sel.chosen:
            print(f"  {GREY}-  {n:<26} {why}{RESET}")
    return 0


def cmd_models(args) -> int:
    """Show the model catalogue and the policy that picks between them."""
    table = model_planner.catalogue()
    print()
    print(f"  {BOLD}MODEL CATALOGUE{RESET}")
    for alias in model_planner.LADDER:
        mark = f"  {GREEN}<- default{RESET}" if alias == model_planner.default_alias() else ""
        print(f"    {alias:<10}{table[alias]}{mark}")
    print(f"    {'default':<10}{table['default']}  {GREY}(alias){RESET}")
    print(f"\n  {GREY}Override the default with {model_planner.DEFAULT_MODEL_ENV}=<alias>, "
          f"or the whole table with {model_planner.MODELS_FILE_ENV}=<path to json>.{RESET}")
    print(f"\n  {BOLD}POLICY{RESET}")
    print(f"    Start at the default. Upgrade only when a task scores "
          f"{model_planner.UPGRADE_AT}/10 or more on")
    print(f"    reasoning weight, priority, effort, project complexity, previous "
          f"attempts and domain risk.")
    print(f"    Mechanical roles ({', '.join(sorted(model_planner.MECHANICAL))}) drop below it.")
    print(f"    A failure escalates one step, once, and the escalation is recorded.")
    print(f"\n  {BOLD}REASONING WEIGHT BY ROLE{RESET}")
    by_weight: dict[int, list[str]] = {}
    for name, w in model_planner.REASONING_WEIGHT.items():
        by_weight.setdefault(w, []).append(name)
    for w in sorted(by_weight, reverse=True):
        print(f"    {w}  {GREY}{', '.join(sorted(by_weight[w]))}{RESET}")
    missing = sorted(n for n in roster.REGISTRY if n not in model_planner.REASONING_WEIGHT)
    if missing:
        print(f"\n  {YELLOW}unweighted (treated as 1):{RESET} {', '.join(missing)}")
    return 0


# ---------------------------------------------------------------------------
# Packaging and GitHub
# ---------------------------------------------------------------------------


def cmd_package(args) -> int:
    """Build a clean, reproducible submission package."""
    root = Path(args.project).resolve()
    rules = packaging.PackageRules.load(root)
    if args.dry_run:
        plan = packaging.plan_package(root, rules)
        print()
        print(plan.render())
        return 0 if plan.ready else 1
    try:
        plan = packaging.build_package(root, out=args.out, rules=rules, force=args.force)
    except packaging.PackageBlocked as e:
        print(f"\n{RED}packaging blocked{RESET}\n")
        print(e)
        print(f"\n{GREY}Remove the secret, or re-run with --force if you have "
              f"confirmed it is a false positive.{RESET}")
        return 1
    print()
    print(plan.render())
    print(f"\n{GREEN}packaged{RESET} {root / args.out}")
    return 0


def cmd_github(args) -> int:
    root = Path(args.project).resolve()
    action = args.action

    if action == "init":
        report = gh.init(root)
        print()
        print(report.render())
        print(f"\nNext: hackathon github prepare --project {root}")
        return 0

    if action == "prepare":
        report = gh.prepare(root)
        print()
        print(report.render())
        if report.ready:
            print(f"\n{GREY}Nothing has been sent anywhere. To publish: "
                  f"hackathon github push --yes{RESET}")
        return 0 if report.ready else 1

    if action == "push":
        report = gh.prepare(root)
        print()
        print(report.render())
        if not report.ready:
            print(f"\n{RED}refusing to push{RESET} — resolve the problems above first.")
            return 1

        # The confirmation gate. Push is the only outward-facing action in this
        # whole system, and it is the one place where being wrong is public.
        if not args.yes:
            print(f"\n{BOLD}This will publish {len(report.plan.include)} files"
                  f"{' to ' + (args.create or report.remote or 'origin')}.{RESET}")
            try:
                answer = input("  push? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer not in ("y", "yes"):
                print(f"{GREY}not pushed{RESET}")
                return 1

        try:
            out = gh.push(
                root, confirmed=True, message=args.message, remote=args.remote,
                branch=args.branch, create=args.create, private=not args.public,
            )
        except (gh.PushRefused, gh.GitUnavailable, packaging.PackageBlocked) as e:
            print(f"\n{RED}{e}{RESET}")
            return 1
        print()
        print(out.render())
        return 0 if out.pushed or out.committed else 1

    print(f"{RED}unknown github action:{RESET} {action}")
    return 1


def cmd_graph(args) -> int:
    state = _project(args)
    print(state.graph.ascii() or "no plan yet")
    return 0


def cmd_handoffs(args) -> int:
    state = _project(args)
    if args.json:
        print(json.dumps([r.to_dict() for r in state.history], indent=2))
        return 0
    for r in state.history:
        print(f"\n{BOLD}{r.agent}{RESET} [{r.status.value}] {GREY}{r.task_id}{RESET}")
        print(f"  {r.summary[:400]}")
        for a in r.artifacts:
            print(f"    {GREY}artifact:{RESET} {a.path} ({a.bytes}B)")
        for f in r.findings:
            print(f"    {GREY}{f.severity.value}:{RESET} {f.summary[:120]}")
        for x in r.assumptions:
            print(f"    {YELLOW}assumed:{RESET} {x[:120]}")
    return 0


# ---------------------------------------------------------------------------


def cmd_auth(args) -> int:
    """Report which credential a run would use, and whether we accept it.

    Deliberately makes no model request: an authentication check that costs
    usage is one people stop running.
    """
    status = subscription_auth.probe()
    print()
    print(status.render())
    print()
    if status.ok:
        print(f"  {GREEN}Ready.{RESET} Runs are served by your Claude subscription; "
              f"no API key is used and nothing is billed per token.")
        print(f"  {GREY}Usage counts against your plan's five-hour and seven-day windows "
              f"(Opus has its own weekly window -- see `hackathon.py routing`).{RESET}")
        return 0

    print(f"  {BOLD}To authenticate with your existing Claude account:{RESET}")
    print()
    print(f"    1. {BOLD}claude{RESET}                 {GREY}# run once, log in through the browser{RESET}")
    print(f"       {GREY}or, for an unattended machine:{RESET}")
    print(f"       {BOLD}claude setup-token{RESET}     {GREY}# then set CLAUDE_CODE_OAUTH_TOKEN to the printed token{RESET}")
    print(f"    2. {BOLD}pip install claude-agent-sdk{RESET}")
    print(f"    3. {BOLD}python hackathon.py auth{RESET}  {GREY}# should print OK{RESET}")
    print()
    print(f"  {GREY}Requires a Claude Pro, Max, Team or Enterprise plan. "
          f"This system has no paid-API fallback by design.{RESET}")
    return 1


def cmd_routing(args) -> int:
    """Show the model each specialist runs on, and why."""
    plan = routing.plan(roster.REGISTRY)
    print()
    for name in reversed(routing.ORDER):
        tier = routing.TIERS[name]
        members = plan.get(name, [])
        print(f"  {BOLD}{name}{RESET}  {tier.model}  {GREY}effort={tier.effort}, "
              f"max_turns={tier.max_turns}{RESET}")
        print(f"    {GREY}{tier.why}{RESET}")
        for m in members:
            print(f"      {GREY}-{RESET} {roster.get(m).title}")
        print()
    missing = routing.unrouted(roster.REGISTRY)
    if missing:
        print(f"  {YELLOW}unrouted (falling back to {routing.DEFAULT_TIER}):{RESET} "
              f"{', '.join(missing)}")
    print(f"  {GREY}Only the 'deep' tier draws on your plan's separate weekly Opus "
          f"window. Force one tier for a whole run with HACKATHON_TIER=standard.{RESET}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hackathon",
        description="An autonomous hackathon development environment.",
    )
    ap.add_argument("--project", default=".", help="project directory (default: cwd)")
    ap.add_argument(
        "--backend", default="auto",
        choices=("auto", "subscription", "simulated", "anthropic"),
        help="auto/subscription: your Claude subscription via the Agent SDK (default). "
             "simulated: no model at all. anthropic: paid API key, requires "
             "HACKATHON_ALLOW_PAID_API=1.",
    )
    ap.add_argument(
        "--no-cache", action="store_true",
        help="re-run every task even if the ledger already has an identical one",
    )
    ap.add_argument("--parallel", type=int, default=3, help="max concurrent specialists")
    ap.add_argument("--approve", action="store_true", help="prompt before every write/shell call")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument(
        "--model", default="",
        help="force every task onto one model (default/sonnet/opus/haiku). "
             "Without this, the model planner picks per task, starting from the "
             "default and upgrading only where it can justify it.",
    )
    ap.add_argument(
        "--no-optimize", action="store_true",
        help="send raw context digests instead of prioritised, deduplicated ones",
    )
    ap.add_argument(
        "--no-planner", action="store_true",
        help="skip the Claude capability planner; use deterministic rules only",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a new hackathon project")
    p.add_argument("name", nargs="?", help="project folder name")
    p.add_argument("--problem", required=True, help="problem statement, or a path to it")
    p.add_argument("--judging", help="judging criteria, or a path")
    p.add_argument("--submission", help="submission requirements, or a path")
    p.add_argument("--constraints", help="constraints, or a path")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("agents", help="list specialists, or describe one")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("tools", help="list the tool registry")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("auth", help="check subscription authentication (makes no model request)")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("routing", help="show which model each specialist is routed to")
    p.set_defaults(func=cmd_routing)

    p = sub.add_parser("plan", help="select specialists and build the task graph")
    p.add_argument("--depth", default="full", choices=("full", "lean"))
    p.add_argument("--verbose-skips", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("select", help="preview specialist selection for a brief")
    p.add_argument("problem", help="problem statement, or a path to it")
    p.add_argument("--depth", default="full", choices=("full", "lean"))
    p.add_argument("--planner", action="store_true",
                   help="also run the Claude capability planner (costs one call)")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("models", help="show the model catalogue and selection policy")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("status", help="the dashboard")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("tasks", help="list tasks and their state")
    p.set_defaults(func=cmd_tasks)

    p = sub.add_parser("graph", help="print the dependency tree")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("handoffs", help="show what each specialist reported")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_handoffs)

    p = sub.add_parser("run", help="let the Orchestrator work until it cannot")
    p.add_argument("--once", action="store_true", help="run a single wave")
    p.add_argument("--task", help="run one specific task by id")
    p.add_argument("--max-waves", type=int, default=25)
    p.add_argument("--depth", default="full", choices=("full", "lean"))
    p.add_argument("--dry-run", action="store_true", help="no writes; show what would happen")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="re-queue failures and continue")
    p.add_argument("--max-waves", type=int, default=25)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("package", help="build a clean submission package")
    p.add_argument("--out", default=packaging.PACKAGE_DIR,
                   help=f"destination inside the project (default {packaging.PACKAGE_DIR})")
    p.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    p.add_argument("--force", action="store_true",
                   help="package despite a secret-scan hit you have confirmed is a "
                        "false positive; recorded in the package status")
    p.set_defaults(func=cmd_package)

    p = sub.add_parser("github", help="prepare a GitHub-ready repository")
    p.add_argument("action", choices=("init", "prepare", "push"))
    p.add_argument("--yes", action="store_true",
                   help="skip the interactive confirmation on push (still requires "
                        "a passing secret scan)")
    p.add_argument("--message", default="Hackathon submission", help="commit message")
    p.add_argument("--remote", default="origin")
    p.add_argument("--branch", default="")
    p.add_argument("--create", default="",
                   help="create the repo with the GitHub CLI, e.g. owner/name")
    p.add_argument("--public", action="store_true",
                   help="with --create, make it public (default private)")
    p.set_defaults(func=cmd_github)

    taken = set(sub.choices)
    for verb in PHASE_TASKS:
        if verb in taken:  # a stage verb must never shadow a real command
            raise ValueError(f"stage verb '{verb}' collides with a command")
        p = sub.add_parser(verb, help=f"run the {verb} stage only")
        p.set_defaults(func=lambda a, v=verb: cmd_phase(a, v))

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except NoSubscriptionAuth as e:
        print(f"\n  {RED}cannot start{RESET}\n  {e}\n")
        print(f"  {GREY}Run `python hackathon.py auth` for the exact setup commands.{RESET}")
        return 2
    except UsageLimitReached as e:
        print(f"\n  {RED}stopped{RESET} {e}\n")
        return 3
    except model_planner.UnknownModel as e:
        print(f"\n  {RED}bad --model{RESET}\n  {e}\n")
        print(f"  {GREY}`python hackathon.py models` lists what is configured.{RESET}")
        return 2
    except (packaging.PackageBlocked, gh.PushRefused, gh.GitUnavailable) as e:
        print(f"\n  {RED}{type(e).__name__}{RESET}\n  {e}\n")
        return 1
    except FileNotFoundError as e:
        print(f"{RED}{e}{RESET}")
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
