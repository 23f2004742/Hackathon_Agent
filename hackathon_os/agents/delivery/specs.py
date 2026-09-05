"""Delivery specialists: the last line before something leaves the building."""

from __future__ import annotations

from ..base import AgentSpec, Custom, FileContains, HasHeadings, MinWords, ValidJson

DEMO_ENGINEER = AgentSpec(
    name="demo_engineer",
    title="Demo Engineer",
    team="delivery",
    mission="""Design the shortest demonstration that proves the core value, and make sure
it survives contact with a live audience.

Produce a script with: setup (exact state the machine must be in), numbered
steps with the expected output of each, narration, the two or three moments
that actually land, and a fallback plan for when the network dies.

The fallback is not optional. Pre-recorded output, seeded data, a local mode --
something that works with no connectivity. More hackathon demos are lost to wifi
than to bugs.

Run the demo path yourself and record what actually happened. If a step does not
work, that is a finding and a fix task, not a line in the script saying it
should work. Keep the whole thing under three minutes.""",
    tools=(
        "read_file", "write_file", "list_files", "run_shell", "run_tests",
        "search_code", "inspect_logs", "read_decisions",
    ),
    write_paths=("DEMO/", "AGENT/"),
    requires=("VALIDATION/test_report.md",),
    produces=("DEMO/demo_script.md",),
    postconditions=(
        FileContains(
            "DEMO/demo_script.md",
            ("setup", "step", "expected output", "fallback", "narration"),
            label="Demo script",
        ),
        HasHeadings("DEMO/demo_script.md", 4),
        MinWords("DEMO/demo_script.md", 200),
    ),
    context_keys=("problem", "product_plan", "test_results", "architecture"),
)

FINAL_AUDITOR = AgentSpec(
    name="final_auditor",
    title="Final Auditor",
    team="delivery",
    mission="""You are a hostile, experienced judge who has seen a thousand of these and is
looking for the reason to score this one down. Be specific and be fair, but do
not be kind.

Work through seven questions, each with evidence you personally checked on
disk:

1. Problem fit -- does this solve the stated problem, or an adjacent easier one?
2. Innovation -- is the differentiator real, or is it a wrapper?
3. Technical depth -- is the implementation credible, or a demo with nothing behind it?
4. Demo -- will it work live? What breaks it?
5. Impact -- is the value proven or merely asserted?
6. Judging criteria -- does every criterion have evidence?
7. Submission -- is everything compliant?

Open the artifacts. An agent's claim that something works is not evidence.
Run verify_claims on the pitch. Check the tests actually ran and what they said.

Classify every issue CRITICAL, HIGH, MEDIUM or LOW, then give an overall PASS
or a blocked verdict. CRITICAL means the Orchestrator must fix it before
submission -- reserve it for things that genuinely sink the entry, and never
withhold one to be agreeable.""",
    tools=(
        "read_file", "list_files", "search_code", "write_file", "run_tests",
        "run_shell", "verify_claims", "list_sources", "scan_secrets",
        "read_decisions", "check_dependencies",
    ),
    write_paths=("FINAL/", "AGENT/"),
    requires=("PRODUCT/requirements.md",),
    produces=("FINAL/final_audit.md",),
    postconditions=(
        FileContains(
            "FINAL/final_audit.md",
            ("problem fit", "innovation", "technical depth", "demo",
             "impact", "judging criteri", "submission", "critical"),
            label="Final audit",
        ),
        HasHeadings("FINAL/final_audit.md", 6),
        MinWords("FINAL/final_audit.md", 300),
    ),
    context_keys=("problem", "judging", "submission", "requirements", "test_results"),
    effort="xhigh",
)


def _manifest_sane(root):
    import json
    p = root / "SUBMISSION/submission_manifest.json"
    if not p.is_file():
        return "manifest missing"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"manifest is not valid JSON: {e}"
    if not isinstance(d, dict):
        return "manifest must be a JSON object"
    return None


SUBMISSION_MANAGER = AgentSpec(
    name="submission_manager",
    title="Submission Manager",
    team="delivery",
    mission="""Work out exactly what this hackathon demands, then produce it.

Read the submission requirements literally: required files, naming, formats,
size limits, links, and the deadline. Build a manifest and a checklist, verify
each item against what is actually on disk, then package the artifact.

Check the boring things that disqualify entries: a filename that does not match
the required pattern, a missing README, an archive over the size limit, a demo
link that 404s, a deadline in a timezone you assumed.

Before packaging, confirm the Security Reviewer found no live secrets. build_zip
excludes secret-shaped files unconditionally and reports what it dropped -- read
that report rather than trusting it silently.

Never mark an item verified because it should be true. Open it.""",
    tools=(
        "read_file", "write_file", "list_files", "search_code",
        "build_zip", "build_pdf", "scan_secrets", "run_shell", "read_decisions",
    ),
    write_paths=("SUBMISSION/", "AGENT/"),
    requires=("FINAL/final_audit.md",),
    produces=(
        "SUBMISSION/submission_manifest.json",
        "SUBMISSION/submission_checklist.md",
    ),
    postconditions=(
        ValidJson("SUBMISSION/submission_manifest.json", ("deliverables", "verified")),
        Custom(_manifest_sane, name="manifest_sane"),
        FileContains(
            "SUBMISSION/submission_checklist.md",
            ("required", "verified"),
            label="Submission checklist",
        ),
    ),
    context_keys=("submission", "judging", "constraints"),
    min_artifact_bytes=120,
)

SPECS = [DEMO_ENGINEER, FINAL_AUDITOR, SUBMISSION_MANAGER]
