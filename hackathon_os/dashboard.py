"""The status dashboard."""

from __future__ import annotations

from .handoff import Status
from .state import PHASES, ProjectState

from .glyphs import BOLD, G, GREEN, GREY, RED, RESET, YELLOW
from .glyphs import bar as _bar
from .glyphs import rule
BAR = rule(52)

MARKS = {
    None: f"{GREY}○{RESET}",
    Status.COMPLETED: f"{GREEN}✓{RESET}",
    Status.FAILED: f"{RED}✗{RESET}",
    Status.BLOCKED: f"{YELLOW}⚠{RESET}",
    Status.SKIPPED: f"{GREY}–{RESET}",
    Status.NEEDS_HUMAN: f"{YELLOW}?{RESET}",
}


def _specialists(state: ProjectState) -> list[str]:
    """Who is on the team, and how many were deliberately left off."""
    from . import agents as roster

    sel = state.selection
    if not sel.chosen:
        return []
    skipped = {k for k in sel.skipped if k not in sel.chosen}
    out = [f"{BOLD}SPECIALISTS{RESET}",
           f"  Selected  {len(sel.chosen)} / {len(roster.REGISTRY)}"
           f"    {GREY}skipped {len(skipped)}{RESET}"]
    if sel.analysis.project_type:
        out.append(f"  Type      {' + '.join(sel.analysis.project_type)}"
                   f"    {GREY}complexity {sel.analysis.complexity}/5{RESET}")
    out.append(f"  Chosen by {'rules + capability planner' if sel.planner_used else 'rules'}")
    if state.replans:
        last = state.replans[-1]
        out.append(f"  {YELLOW}replans{RESET}   {len(state.replans)} — last added "
                   f"{', '.join(last.get('tasks_added', []))} "
                   f"{GREY}({', '.join(last.get('capabilities', []))}){RESET}")
    out.append("")
    return out


def _models(state: ProjectState) -> list[str]:
    if not state.model_decisions:
        return []
    counts: dict[str, int] = {}
    escalated = 0
    for row in state.model_decisions.values():
        counts[row.get("model", "?")] = counts.get(row.get("model", "?"), 0) + 1
        if row.get("escalations"):
            escalated += 1
    out = [f"{BOLD}MODELS{RESET}"]
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        out.append(f"  {name:<10}{n} task(s)")
    if escalated:
        out.append(f"  {YELLOW}escalated{RESET} {escalated} task(s) after failure")
    out.append("")
    return out


def _tokens(state: ProjectState) -> list[str]:
    m = state.token_metrics
    if not m.optimised_tasks:
        return []
    budgeted = m.estimated_output_tokens
    return [
        f"{BOLD}TOKEN OPTIMIZATION{RESET}",
        f"  Estimated input   {m.estimated_input_tokens:,} tokens sent",
        f"  Estimated output  {budgeted:,} tokens budgeted",
        f"  Context saved     {m.context_tokens_removed:,} tokens "
        f"{GREY}({(1 - m.context_compression_ratio) * 100:.0f}% of candidate context){RESET}",
        f"  Compression ratio {m.context_compression_ratio}",
        f"  Cache hits        {m.cache_hits}",
        f"  Cache misses      {m.cache_misses}",
        f"  {GREY}deduplicated {m.deduped_blocks} block(s), compressed "
        f"{m.compressed_items}, dropped {m.dropped_items} low-priority slice(s){RESET}",
        "",
    ]


def _package(state: ProjectState) -> list[str]:
    from .github import summary as gh_summary
    from .packaging import read_status

    st = read_status(state.root)
    ghs = gh_summary(state.root)
    if not st and not ghs.get("initialised"):
        return []
    scan = st.get("secret_scan", "not run")
    mark = GREEN if scan == "PASS" else (RED if scan == "FAIL" else GREY)
    ready = "yes" if (ghs.get("ready") and scan == "PASS") else "no"
    return [
        f"{BOLD}PACKAGE{RESET}",
        f"  Package status    {'built' if st.get('built') else 'not built'}"
        + (f"  {GREY}{st.get('file_count', 0)} files, "
           f"{st.get('bytes', 0) / 1024:.0f} KB{RESET}" if st.get("built") else ""),
        f"  Secret scan       {mark}{scan}{RESET}",
        f"  GitHub readiness  {ready}"
        f"  {GREY}(repo {'initialised' if ghs.get('initialised') else 'not initialised'},"
        f" gh cli {'available' if ghs.get('gh_cli') else 'absent'}){RESET}",
        "",
    ]


def render(state: ProjectState, next_action: str = "", gates: list[str] | None = None) -> str:
    g = state.graph
    counts = g.counts()
    pct = g.progress
    bar = _bar(pct, 20)

    out: list[str] = [
        BAR,
        f" {BOLD}HACKATHON: {state.name}{RESET}",
        BAR,
        "",
        f"Phase: {BOLD}{state.phase.upper()}{RESET}"
        f"    Backend: {state.backend}",
        f"Progress: {bar} {pct * 100:.0f}%"
        f"  {GREY}({counts['completed']}/{counts['total']} tasks){RESET}",
        "",
        f"{BOLD}TASKS{RESET}",
        f"  Completed {counts['completed']}   Pending {counts['pending']}   "
        f"Failed {counts['failed']}   Blocked {counts['blocked']}   "
        f"Skipped {counts['skipped']}",
        "",
    ]

    if not g.tasks:
        out += ["No plan yet. Run: hackathon plan", "", BAR]
        return "\n".join(out)

    by_phase = g.by_phase()
    for phase in PHASES:
        tasks = by_phase.get(phase)
        if not tasks:
            continue
        out.append(f"{GREY}{phase.upper()}{RESET}")
        for t in sorted(tasks, key=lambda x: x.sort_key):
            mark = MARKS.get(t.status, G['bullet'])
            flag = f" {GREY}(optional){RESET}" if t.optional and t.status is None else ""
            out.append(f"  {mark} {t.id:<16} {GREY}{t.agent}{RESET}{flag}")
        out.append("")

    active = [t for t in g.tasks.values() if t.status is None]
    ready_ids = {t.id for t in g.ready()}
    if active:
        out.append(f"{BOLD}AGENT QUEUE{RESET}")
        for t in sorted(active, key=lambda x: x.sort_key)[:6]:
            label = f"{GREEN}READY{RESET}" if t.id in ready_ids else f"{GREY}WAITING{RESET}"
            out.append(f"  {t.agent:<24} {label}")
        out.append("")

    blockers = state.blockers()
    out.append(f"{BOLD}BLOCKERS{RESET}")
    if blockers:
        out += [f"  {RED}{G['bullet']}{RESET} {b}" for b in blockers[:6]]
    else:
        out.append(f"  {GREY}None{RESET}")
    out.append("")

    if gates:
        out.append(f"{BOLD}NEEDS A HUMAN{RESET}")
        out += [f"  {YELLOW}{G['bullet']}{RESET} {x}" for x in gates[:5]]
        out.append("")

    out += _specialists(state)
    out += _models(state)
    out += _tokens(state)
    out += _package(state)

    cost = state.cost()
    out.append(
        f"{GREY}cost: {cost['agent_runs']} agent runs, {cost['tool_calls']} tool calls, "
        f"{cost['input_tokens'] + cost['output_tokens']:,} tokens measured{RESET}"
    )
    out.append("")
    out.append(f"{BOLD}NEXT BEST ACTION{RESET}")
    out.append(f"  {next_action or 'Run: hackathon run'}")
    out.append(BAR)
    return "\n".join(out)
