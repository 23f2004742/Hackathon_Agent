"""Security tooling. Given only to the Security Reviewer and Final Auditor.

`scan_secrets` is a real scanner, not a prompt asking the model to look
carefully -- it runs over the tree and returns file:line hits. The model's job
is to triage what it finds, which is the part that actually needs judgement.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import fail, guard, tool, truncate
from .filesystem import SKIP_DIRS

# (label, regex, severity). Deliberately tuned to catch real key shapes rather
# than every string containing the word "key", which would drown the signal.
SECRET_RULES: list[tuple[str, str, str]] = [
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_\-]{20,}", "CRITICAL"),
    ("OpenAI API key", r"\bsk-(?!ant-)[A-Za-z0-9]{32,}", "CRITICAL"),
    ("AWS access key id", r"\bAKIA[0-9A-Z]{16}\b", "CRITICAL"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}", "CRITICAL"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{30,}", "CRITICAL"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}", "CRITICAL"),
    ("Private key block", r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----", "CRITICAL"),
    ("JWT", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "HIGH"),
    ("Hardcoded password assignment", r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{6,}['\"]", "HIGH"),
    ("Generic secret assignment", r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}['\"]", "HIGH"),
    ("Database URL with credentials", r"(?i)\b(?:postgres|mysql|mongodb)(?:\+\w+)?://[^\s:'\"]+:[^\s@'\"]+@", "CRITICAL"),
]

# Values that look like secrets but are obviously placeholders.
PLACEHOLDER = re.compile(
    r"(?i)(your[_-]?|example|placeholder|xxx+|\.\.\.|<[a-z_]+>|dummy|sample|changeme|test[_-]?key|foo|bar)"
)

CODE_RULES: list[tuple[str, str, str]] = [
    ("shell=True with interpolation", r"subprocess\.(?:run|Popen|call)\([^)]*f['\"]", "HIGH"),
    ("eval on non-literal", r"\beval\s*\(\s*(?!['\"])", "HIGH"),
    ("exec on non-literal", r"\bexec\s*\(\s*(?!['\"])", "HIGH"),
    ("SQL built by string formatting", r"(?i)(?:SELECT|INSERT|UPDATE|DELETE)\s+.*?['\"]\s*(?:\+|%|\.format\(|f['\"])", "HIGH"),
    ("pickle.loads on untrusted data", r"\bpickle\.loads?\s*\(", "MEDIUM"),
    ("TLS verification disabled", r"verify\s*=\s*False", "HIGH"),
    ("Binding to all interfaces", r"['\"]0\.0\.0\.0['\"]", "MEDIUM"),
    ("Debug mode enabled", r"(?i)debug\s*=\s*True", "MEDIUM"),
    ("CORS fully open", r"(?i)allow_origins\s*=\s*\[\s*['\"]\*['\"]", "MEDIUM"),
]

SCANNABLE = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yml", ".yaml", ".toml",
    ".env", ".sh", ".ps1", ".md", ".txt", ".cfg", ".ini", ".html", ".sql",
}


def _walk(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in SCANNABLE or p.name.startswith(".env"):
            if p.stat().st_size <= 2_000_000:
                yield p, rel


@tool("security")
def scan_secrets(path: str = ".") -> str:
    """Scan the project for committed secrets and credentials.

    Run this before any packaging or deployment step. Obvious placeholders
    (\"your-key-here\", \"sk-ant-...\") are reported separately from live-looking
    values so you can tell a template from a leak.

    Args:
        path: Subtree to scan, relative to project root. Default the whole project.
    """
    try:
        ctx = guard("scan_secrets")
        base = ctx.resolve(path)
        if not base.exists():
            return f"no such path: {path}"
        root = base if base.is_dir() else base.parent
        real: list[str] = []
        placeholders: list[str] = []
        scanned = 0

        for p, rel in _walk(root):
            scanned += 1
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if len(line) > 1000:
                    continue
                for label, pattern, sev in SECRET_RULES:
                    m = re.search(pattern, line)
                    if not m:
                        continue
                    snippet = m.group(0)[:24] + ("..." if len(m.group(0)) > 24 else "")
                    row = f"  [{sev}] {label}\n      {rel.as_posix()}:{i}  {snippet}"
                    (placeholders if PLACEHOLDER.search(line) else real).append(row)
                    break

        head = f"scanned {scanned} files under {path}\n"
        if not real and not placeholders:
            return head + "PASS: no secrets detected."
        out = head
        if real:
            out += f"\nLIVE-LOOKING SECRETS ({len(real)}) -- must be removed before submission:\n" + "\n".join(real)
        if placeholders:
            out += f"\n\nPLACEHOLDERS ({len(placeholders)}) -- likely safe, confirm each:\n" + "\n".join(placeholders)
        return truncate(out)
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("security")
def scan_code_security(path: str = ".") -> str:
    """Scan source for common insecure patterns: injection, eval, disabled TLS.

    Findings are candidates, not verdicts. Read each in context before
    reporting it -- `verify=False` in a throwaway script is not the same
    finding as `verify=False` against a payment API.

    Args:
        path: Subtree to scan, relative to project root. Default whole project.
    """
    try:
        ctx = guard("scan_code_security")
        base = ctx.resolve(path)
        if not base.exists():
            return f"no such path: {path}"
        root = base if base.is_dir() else base.parent
        hits: list[str] = []
        scanned = 0
        for p, rel in _walk(root):
            if p.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx", ".sql", ".sh"}:
                continue
            scanned += 1
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                for label, pattern, sev in CODE_RULES:
                    if re.search(pattern, line):
                        hits.append(f"  [{sev}] {label}\n      {rel.as_posix()}:{i}  {stripped[:120]}")
                        break
        if not hits:
            return f"scanned {scanned} source files: no insecure patterns detected."
        return truncate(f"scanned {scanned} source files, {len(hits)} candidate finding(s):\n" + "\n".join(hits))
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("security")
def check_dependencies() -> str:
    """List declared dependencies and flag unpinned ones.

    Unpinned versions are a reproducibility risk for judges rebuilding your
    project, and a supply-chain risk in general.
    """
    try:
        ctx = guard("check_dependencies")
        found: list[str] = []
        for name in ("requirements.txt", "pyproject.toml", "package.json"):
            p = ctx.root / name
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            found.append(f"--- {name} ---")
            if name == "requirements.txt":
                for line in text.splitlines():
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    pinned = any(op in s for op in ("==", ">=", "~=", "<="))
                    found.append(f"  {'OK  ' if pinned else 'LOOSE'} {s}")
            else:
                found.append(truncate(text, 2000))
        return "\n".join(found) if found else "no dependency manifest found"
    except Exception as e:  # noqa: BLE001
        return fail(e)
