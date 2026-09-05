"""Turning a finished hackathon project into something you can hand over.

The project tree the specialists work in is not the thing you submit. It
carries the agent's own state, its ledger, its cache, four kinds of scratch
directory and -- if anyone was careless -- a `.env`. Packaging is the step that
decides what a judge or a GitHub repository actually receives.

Two rules shape everything here:

1. **Nothing ships that looks like a live secret.** The scan runs before the
   copy, not after, and a live-looking hit *blocks* the package rather than
   warning about it. A warning at 3am the night before a deadline is a warning
   nobody reads.

2. **Exclusion is by default, inclusion is by decision.** Caches, virtualenvs,
   checkpoints, node_modules and the agent's own bookkeeping are dropped unless
   a project rule explicitly asks for them, so a new kind of junk directory
   does not silently end up in the archive.

The output is a plain directory, not an archive: it is diffable, inspectable,
and it is what `github.py` commits.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .tools.security import PLACEHOLDER, SECRET_RULES

# Deliberately not "submission/": the project already has a SUBMISSION/
# directory owned by the Submission Manager, and on a case-insensitive
# filesystem the package would land inside its own input.
PACKAGE_DIR = "dist/submission"
RULES_FILE = "AGENT/package_rules.json"
STATUS_FILE = "AGENT/package_status.json"

#: Directories that never belong in a submission or a repository.
EXCLUDE_DIRS = (
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "node_modules", ".idea", ".vscode", "dist", "build",
    ".ipynb_checkpoints", ".tox", ".cache", "site-packages",
)

#: File patterns excluded by name. Secrets, junk and model weights.
EXCLUDE_FILES = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
    "*credentials*", "*secret*", "*.log", "*.tmp", "*.swp", "*.pyc", "*.pyo",
    "*.so", "*.dylib", "*.dll", "*.ckpt", "*.pt", "*.pth", "*.h5", "*.onnx",
    "*.pkl", "*.joblib", "*.sqlite", "*.db", ".DS_Store", "Thumbs.db",
)

#: These look like the patterns above but carry no secret and belong in the
#: package -- they are how a judge learns what configuration to supply.
KEEP_ANYWAY = (".env.example", ".env.sample", ".env.template")

#: The agent's own bookkeeping. Interesting to us, noise to a judge.
AGENT_ONLY = ("AGENT/state.json", "AGENT/cache", "AGENT/package_status.json",
              "AGENT/package_rules.json")

#: Where each part of the project tree lands in the package.
LAYOUT: tuple[tuple[str, str], ...] = (
    ("src", "source"),
    ("tests", "tests"),
    ("data", "data"),
    ("DOCUMENTATION", "docs"),
    ("PRODUCT", "docs/product"),
    ("RESEARCH", "docs/research"),
    ("DESIGN", "docs/design"),
    ("VALIDATION", "docs/validation"),
    ("FINAL", "docs/audit"),
    ("DEMO", "demo"),
    ("PRESENTATION", "presentation"),
    ("SUBMISSION", "submission"),
    ("AGENT", "docs/agent"),
)

#: Files copied to the package root as-is when present.
ROOT_FILES = (
    "README.md", "LICENSE", "LICENSE.md", "requirements.txt", "pyproject.toml",
    "package.json", "Makefile", "Dockerfile", "docker-compose.yml",
    ".env.example", ".gitignore",
)

MAX_FILE_BYTES = 25 * 1024 * 1024      # a judge downloading 300MB is a lost judge


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass
class PackageRules:
    """Project-specific inclusion and exclusion, layered over the defaults."""

    include: tuple[str, ...] = ()      # globs forced in even if excluded
    exclude: tuple[str, ...] = ()      # extra globs forced out
    max_file_bytes: int = MAX_FILE_BYTES

    @classmethod
    def load(cls, root: Path) -> "PackageRules":
        p = Path(root) / RULES_FILE
        if not p.is_file():
            return cls()
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            include=tuple(d.get("include", ())),
            exclude=tuple(d.get("exclude", ())),
            max_file_bytes=int(d.get("max_file_bytes", MAX_FILE_BYTES)),
        )


# ---------------------------------------------------------------------------
# Secret scanning
# ---------------------------------------------------------------------------


@dataclass
class SecretHit:
    path: str
    line: int
    label: str
    severity: str
    snippet: str
    placeholder: bool = False

    def to_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "label": self.label,
                "severity": self.severity, "snippet": self.snippet,
                "placeholder": self.placeholder}


@dataclass
class ScanReport:
    live: list[SecretHit] = field(default_factory=list)
    placeholders: list[SecretHit] = field(default_factory=list)
    blocked_files: list[str] = field(default_factory=list)
    scanned: int = 0

    @property
    def passed(self) -> bool:
        return not self.live and not self.blocked_files

    def render(self) -> str:
        out = ["SECRET SCAN", "-----------"]
        if self.passed:
            out.append(f"[OK] No secrets detected ({self.scanned} files scanned)")
            if self.placeholders:
                out.append(f"     {len(self.placeholders)} placeholder(s) noted, none packaged")
            return "\n".join(out)
        out = ["BLOCKED", "-------", "Potential secret detected:"]
        for f in sorted(set(self.blocked_files)):
            out.append(f"  {f}")
        for h in self.live:
            out.append(f"  {h.path}:{h.line}  {h.label}  {h.snippet}")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed, "scanned": self.scanned,
            "live": [h.to_dict() for h in self.live],
            "placeholders": [h.to_dict() for h in self.placeholders],
            "blocked_files": sorted(set(self.blocked_files)),
        }


SCANNABLE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".yml", ".yaml", ".toml",
    ".env", ".sh", ".ps1", ".md", ".txt", ".cfg", ".ini", ".html", ".sql",
    ".ipynb", ".xml", ".properties",
}

#: Files whose very presence blocks a package, secret content or not.
FORBIDDEN_NAMES = (".env", "credentials.json", "service-account.json",
                   "id_rsa", "id_ed25519", ".npmrc", ".pypirc")


def scan_secrets(root: Path, files: list[Path] | None = None) -> ScanReport:
    """Scan a file set for committed credentials.

    Reuses the same rule table the Security Reviewer's tool uses, so what
    blocks a package is exactly what an agent would have reported.
    """
    root = Path(root)
    report = ScanReport()
    candidates = files if files is not None else _walk_all(root)
    for p in candidates:
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            rel = p.name
        name = Path(rel).name
        if name in FORBIDDEN_NAMES and name not in KEEP_ANYWAY:
            report.blocked_files.append(rel)
            continue
        if p.suffix.lower() not in SCANNABLE_SUFFIXES and not name.startswith(".env"):
            continue
        try:
            if p.stat().st_size > 2_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        report.scanned += 1
        for i, line in enumerate(text.splitlines(), 1):
            if len(line) > 1000:
                continue
            for label, pattern, sev in SECRET_RULES:
                m = re.search(pattern, line)
                if not m:
                    continue
                raw = m.group(0)
                hit = SecretHit(
                    path=rel, line=i, label=label, severity=sev,
                    snippet=raw[:20] + ("..." if len(raw) > 20 else ""),
                    placeholder=bool(PLACEHOLDER.search(line)),
                )
                (report.placeholders if hit.placeholder else report.live).append(hit)
                break
    return report


_EXCLUDE_DIRS_LOWER = frozenset(d.lower() for d in EXCLUDE_DIRS)


def in_excluded_dir(rel: str) -> bool:
    """True if any path segment is a directory we never package.

    Matched case-insensitively: on Windows the tree reports SUBMISSION where
    the table says submission, and a rule that silently stops matching is
    worse than no rule at all.
    """
    return any(part.lower() in _EXCLUDE_DIRS_LOWER for part in Path(rel).parts)


def _walk_all(root: Path, *, skip_prefix: str = ""):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if in_excluded_dir(rel):
            continue
        if skip_prefix and rel.startswith(skip_prefix):
            continue
        yield p


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


@dataclass
class PackagePlan:
    """What would be packaged, what would not, and why."""

    root: Path
    include: list[tuple[Path, str]] = field(default_factory=list)  # (src, dest rel)
    excluded: list[tuple[str, str]] = field(default_factory=list)  # (rel, why)
    scan: ScanReport = field(default_factory=ScanReport)

    @property
    def total_bytes(self) -> int:
        return sum(p.stat().st_size for p, _ in self.include if p.is_file())

    @property
    def ready(self) -> bool:
        return self.scan.passed and bool(self.include)

    def render(self, *, limit: int = 40) -> str:
        out = ["FILES TO COMMIT", "---------------"]
        for _p, dest in self.include[:limit]:
            out.append(f"  {dest}")
        if len(self.include) > limit:
            out.append(f"  ... and {len(self.include) - limit} more")
        out += ["", "FILES EXCLUDED", "--------------"]
        shown: dict[str, int] = {}
        for _rel, why in self.excluded:
            shown[why] = shown.get(why, 0) + 1
        for why, n in sorted(shown.items(), key=lambda kv: -kv[1]):
            out.append(f"  {n:>4}  {why}")
        out += ["", self.scan.render(), "", "PACKAGE SIZE", "------------",
                f"  {len(self.include)} files, {self.total_bytes / 1024:.0f} KB", ""]
        out.append("READY TO COMMIT" if self.ready else "NOT READY — resolve the block above")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {
            "files": [dest for _p, dest in self.include],
            "excluded": [{"path": r, "reason": w} for r, w in self.excluded],
            "bytes": self.total_bytes,
            "ready": self.ready,
            "scan": self.scan.to_dict(),
        }


def _destination(rel: str) -> str | None:
    """Where a project-relative path lands in the package, or None to drop it."""
    parts = Path(rel).parts
    if not parts:
        return None
    if len(parts) == 1:
        return rel if rel in ROOT_FILES else None
    top = parts[0]
    for src, dest in LAYOUT:
        if top == src:
            tail = "/".join(parts[1:])
            return f"{dest}/{tail}" if tail else None
    return None


def _matches(rel: str, patterns) -> bool:
    p = Path(rel)
    return any(p.match(pat) or p.name == pat for pat in patterns)


def plan_package(root: Path, rules: PackageRules | None = None, *,
                 out: str = PACKAGE_DIR) -> PackagePlan:
    """Work out what a package would contain, without writing anything.

    `out` is excluded explicitly rather than only by name, so a previous
    package never ends up inside the next one.
    """
    root = Path(root).resolve()
    rules = rules or PackageRules.load(root)
    plan = PackagePlan(root=root)
    out_prefix = out.rstrip("/") + "/"

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        forced = _matches(rel, rules.include)

        if (in_excluded_dir(rel) or rel.startswith(out_prefix)) and not forced:
            plan.excluded.append((rel, "build/cache/vcs directory"))
            continue
        if Path(rel).name not in KEEP_ANYWAY and _matches(rel, EXCLUDE_FILES) and not forced:
            plan.excluded.append((rel, "secret, log, binary or checkpoint pattern"))
            continue
        if _matches(rel, rules.exclude) and not forced:
            plan.excluded.append((rel, "project package rule"))
            continue
        if any(rel == a or rel.startswith(a + "/") for a in AGENT_ONLY) and not forced:
            plan.excluded.append((rel, "agent bookkeeping, not a deliverable"))
            continue
        try:
            if p.stat().st_size > rules.max_file_bytes and not forced:
                plan.excluded.append((rel, f"larger than {rules.max_file_bytes // 1024 // 1024}MB"))
                continue
        except OSError:
            continue

        dest = _destination(rel)
        if dest is None:
            plan.excluded.append((rel, "not part of the submission layout"))
            continue
        plan.include.append((p, dest))

    # Scan the whole working tree, not only what would be copied. A `.env`
    # is excluded from the package by pattern, but it is still sitting in the
    # directory that `github push` runs `git add -A` over, and a scan that
    # only inspects the files it already decided were safe proves nothing.
    plan.scan = scan_secrets(root, list(_walk_all(root, skip_prefix=out_prefix)))
    return plan


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


class PackageBlocked(RuntimeError):
    """Raised rather than shipping something that looks like a credential."""


def build_package(root: Path, *, out: str = PACKAGE_DIR,
                  rules: PackageRules | None = None,
                  force: bool = False) -> PackagePlan:
    """Materialise the package. Refuses to run if the secret scan fails.

    `force` exists for the case where a scanner false positive is genuinely a
    false positive and the operator has looked at it. It is not the default and
    it is recorded in the package status.
    """
    root = Path(root).resolve()
    plan = plan_package(root, rules, out=out)
    if not plan.scan.passed and not force:
        raise PackageBlocked(plan.scan.render())
    if not plan.include:
        raise PackageBlocked("nothing to package: no eligible files found")

    dest_root = root / out
    if dest_root.exists():
        shutil.rmtree(dest_root)
    for src, rel in plan.include:
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    _write_manifest(root, dest_root, plan, forced=force)
    write_status(root, plan, built=True, forced=force)
    return plan


def _write_manifest(root: Path, dest_root: Path, plan: PackagePlan, *, forced: bool) -> None:
    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": root.name,
        "file_count": len(plan.include),
        "bytes": plan.total_bytes,
        "secret_scan": "PASS" if plan.scan.passed else "OVERRIDDEN" if forced else "FAIL",
        "files": sorted(dest for _p, dest in plan.include),
    }
    (dest_root / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def write_status(root: Path, plan: PackagePlan | None, *, built: bool | None = None,
                 forced: bool | None = None, github: dict | None = None) -> dict:
    """Persist package/GitHub readiness so `status` and `resume` can read it.

    `built` and `forced` default to None rather than False because most callers
    -- `github prepare`, for one -- have an opinion about the scan and none at
    all about whether a package exists. Defaulting them to False let a prepare
    quietly report a built package as unbuilt.
    """
    path = Path(root) / STATUS_FILE
    current: dict = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
    if plan is not None:
        current.update({
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "file_count": len(plan.include),
            "bytes": plan.total_bytes,
            "secret_scan": "PASS" if plan.scan.passed else "FAIL",
            "ready": plan.ready,
        })
    if built is not None:
        current["built"] = built
    if forced is not None:
        current["forced"] = forced
    if github is not None:
        current["github"] = {**current.get("github", {}), **github}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def read_status(root: Path) -> dict:
    path = Path(root) / STATUS_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
