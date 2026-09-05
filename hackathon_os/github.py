"""Getting the project onto GitHub, without ever doing it by surprise.

Three verbs, in order, each of which stops rather than assuming:

  init      make the directory a repository: git init, .gitignore, README
            check, structure validation. Touches nothing remote.
  prepare   show exactly what would be committed, what is excluded, the secret
            scan result and the size. Still touches nothing remote.
  push      the only outward-facing step, and the only one that asks. It
            refuses without an explicit confirmation, refuses on a failed
            secret scan, and never stores a credential of its own.

The last point matters: this module shells out to `git` and, when available,
`gh`. Both already hold the operator's credentials, and neither hands them to
us. A hackathon tool that asks for a GitHub token and writes it to a config
file has created a worse problem than the one it solved.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .packaging import (
    EXCLUDE_DIRS, EXCLUDE_FILES, PackageBlocked, PackagePlan, PackageRules,
    plan_package, read_status, write_status,
)

GITIGNORE = """# --- Python -------------------------------------------------------------
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
.venv/
venv/
env/
.tox/
.mypy_cache/
.pytest_cache/
.ruff_cache/

# --- Node ---------------------------------------------------------------
node_modules/
npm-debug.log*
yarn-error.log*

# --- Secrets. Never commit these. ---------------------------------------
.env
.env.*
!.env.example
!.env.sample
*.pem
*.key
*.p12
*.pfx
id_rsa*
*credentials*
*secret*
.npmrc
.pypirc

# --- Data, models, artifacts --------------------------------------------
*.ckpt
*.pt
*.pth
*.h5
*.onnx
*.pkl
*.joblib
*.sqlite
*.db
data/raw/
data/cache/

# --- Agent bookkeeping (useful locally, noise in a repo) ----------------
AGENT/state.json
AGENT/cache/
AGENT/package_status.json
AGENT/package_rules.json

# --- Build output. `hackathon package` regenerates this from the tree, so
# --- committing it would ship every document twice.
dist/

# --- OS / editor --------------------------------------------------------
.DS_Store
Thumbs.db
.idea/
.vscode/
*.log
*.tmp
"""

README_STUB = """# {name}

> Generated skeleton. The Technical Writer specialist replaces this with the
> real README; if you are reading this in a repository, that step has not run
> yet.

## Problem

_What this project solves._

## Solution

_What was built._

## Setup

```bash
pip install -r requirements.txt
```

## Usage

_How to run it._
"""

#: A README that will not embarrass the project in front of a judge. The
#: technical writer is asked for exactly these; `validate` checks for them.
README_SECTIONS = (
    "problem", "solution", "architecture", "features", "tech stack",
    "setup", "install", "usage", "demo", "limitations", "future work",
    "license",
)


class GitUnavailable(RuntimeError):
    pass


class PushRefused(RuntimeError):
    """Raised when a push was attempted without explicit confirmation."""


def _git_path() -> str:
    path = shutil.which("git")
    if not path:
        raise GitUnavailable(
            "git is not on PATH. Install git, or package with "
            "`hackathon package` and upload the folder manually."
        )
    return path


def git(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    """Run one git command in the project. Never interactive."""
    return subprocess.run(
        [_git_path(), "-C", str(root), *args],
        capture_output=True, text=True, check=check, timeout=180,
    )


def gh_available() -> bool:
    return shutil.which("gh") is not None


def is_repo(root: Path) -> bool:
    try:
        out = git(root, "rev-parse", "--is-inside-work-tree")
    except GitUnavailable:
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def current_branch(root: Path) -> str:
    """The branch name, including on a repository with no commits yet.

    `rev-parse HEAD` fails on an empty repository, which is exactly the state
    `github init` leaves it in -- so the unborn-branch reference is the
    fallback rather than reporting "no branch" on a repo we just created.
    """
    out = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if out.returncode == 0 and out.stdout.strip() not in ("", "HEAD"):
        return out.stdout.strip()
    out = git(root, "symbolic-ref", "--short", "HEAD")
    return out.stdout.strip() if out.returncode == 0 else ""


def remotes(root: Path) -> dict[str, str]:
    out = git(root, "remote", "-v")
    found: dict[str, str] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            found.setdefault(parts[0], parts[1])
    return found


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@dataclass
class InitReport:
    root: Path
    created_repo: bool = False
    wrote_gitignore: bool = False
    wrote_readme: bool = False
    branch: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        out = ["GITHUB INIT", "-----------"]
        out.append(f"  repository   {'initialised' if self.created_repo else 'already present'}"
                   + (f" (branch {self.branch})" if self.branch else ""))
        out.append(f"  .gitignore   {'written' if self.wrote_gitignore else 'already present'}")
        out.append(f"  README.md    {'stub written' if self.wrote_readme else 'already present'}")
        for n in self.notes:
            out.append(f"  note         {n}")
        if self.problems:
            out.append("")
            out.append("VALIDATION")
            for p in self.problems:
                out.append(f"  [!] {p}")
        else:
            out.append("  validation   OK")
        return "\n".join(out)


def validate_repo(root: Path) -> list[str]:
    """Things that would make this repository look careless to a reviewer."""
    problems: list[str] = []
    root = Path(root)
    readme = root / "README.md"
    if not readme.is_file():
        problems.append("no README.md")
    else:
        text = readme.read_text(encoding="utf-8", errors="replace").lower()
        missing = [s for s in ("setup", "usage") if s not in text]
        if missing:
            problems.append(f"README.md does not cover: {', '.join(missing)}")
        if len(text.split()) < 120:
            problems.append("README.md is very short; a judge will read it first")
    if not (root / "requirements.txt").is_file() and not (root / "pyproject.toml").is_file():
        problems.append("no requirements.txt or pyproject.toml — nobody can reproduce this")
    if not (root / ".gitignore").is_file():
        problems.append("no .gitignore")
    if not any((root / d).is_dir() for d in ("src", "app", "lib")):
        problems.append("no src/ directory — is there any code to show?")
    return problems


def init(root: Path, *, write_readme: bool = True) -> InitReport:
    """Make the project a clean, committable repository. No remote contact."""
    root = Path(root).resolve()
    report = InitReport(root=root)

    if not is_repo(root):
        out = git(root, "init")
        if out.returncode != 0:
            raise GitUnavailable(f"git init failed: {out.stderr.strip()}")
        report.created_repo = True
        # A default branch name that matches what GitHub expects, set only on a
        # repository we just created, so we never rename someone else's.
        git(root, "checkout", "-B", "main")
    report.branch = current_branch(root)

    gi = root / ".gitignore"
    if not gi.is_file():
        gi.write_text(GITIGNORE, encoding="utf-8")
        report.wrote_gitignore = True
    elif ".env" not in gi.read_text(encoding="utf-8", errors="replace"):
        # An existing .gitignore that does not exclude secrets is worse than
        # none, because it looks like the question was considered.
        gi.write_text(
            gi.read_text(encoding="utf-8", errors="replace").rstrip()
            + "\n\n# appended by hackathon github init\n" + GITIGNORE,
            encoding="utf-8",
        )
        report.wrote_gitignore = True
        report.notes.append("existing .gitignore did not exclude secrets; appended rules")

    readme = root / "README.md"
    if write_readme and not readme.is_file():
        readme.write_text(README_STUB.format(name=root.name), encoding="utf-8")
        report.wrote_readme = True
        report.notes.append("stub README written; run the technical_writer task to replace it")

    report.problems = validate_repo(root)
    write_status(root, None, github={
        "initialised": True,
        "branch": report.branch,
        "problems": report.problems,
    })
    return report


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def commit_set(root: Path) -> tuple[list[str], list[str]]:
    """(files that would be committed, files git is ignoring).

    Asked of git rather than reconstructed, because git is what will actually
    do the commit. A `prepare` that renders our own idea of the file set and a
    `push` that runs `git add -A` are two different answers to the same
    question, and the one the operator reads would be the wrong one.
    """
    tracked = git(root, "ls-files", "--cached", "--others", "--exclude-standard")
    ignored = git(root, "ls-files", "--others", "--ignored", "--exclude-standard")
    return (
        sorted(x for x in tracked.stdout.splitlines() if x.strip()),
        sorted(x for x in ignored.stdout.splitlines() if x.strip()),
    )


@dataclass
class PrepareReport:
    root: Path
    plan: PackagePlan
    tracked: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    scan: object = None                 # ScanReport over the commit set
    branch: str = ""
    remote: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        total = 0
        for rel in self.tracked:
            p = self.root / rel
            if p.is_file():
                total += p.stat().st_size
        return total

    @property
    def ready(self) -> bool:
        return bool(self.scan and self.scan.passed) and not self.problems

    def render(self, *, limit: int = 40) -> str:
        out = ["FILES TO COMMIT", "---------------"]
        for rel in self.tracked[:limit]:
            out.append(f"  {rel}")
        if len(self.tracked) > limit:
            out.append(f"  ... and {len(self.tracked) - limit} more")
        out += ["", "FILES EXCLUDED", "--------------"]
        for rel in self.ignored[:limit]:
            out.append(f"  {rel}  (.gitignore)")
        if len(self.ignored) > limit:
            out.append(f"  ... and {len(self.ignored) - limit} more ignored")
        if not self.ignored:
            out.append("  (nothing ignored)")
        out += ["", self.scan.render() if self.scan else "SECRET SCAN\n-----------\nnot run"]
        out += ["", "PACKAGE SIZE", "------------",
                f"  {len(self.tracked)} files, {self.bytes / 1024:.0f} KB"]
        out += ["", "REPOSITORY", "----------",
                f"  branch   {self.branch or '(none yet)'}",
                f"  remote   {self.remote or '(none configured)'}"]
        if self.problems:
            out += ["", "PROBLEMS", "--------"]
            out += [f"  [!] {p}" for p in self.problems]
        out += ["", "READY TO COMMIT" if self.ready else "NOT READY"]
        return "\n".join(out)


def prepare(root: Path, rules: PackageRules | None = None) -> PrepareReport:
    """Show exactly what a commit would contain. Touches nothing remote."""
    from .packaging import scan_secrets

    root = Path(root).resolve()
    plan = plan_package(root, rules)
    rep = PrepareReport(root=root, plan=plan)
    if is_repo(root):
        rep.branch = current_branch(root)
        rep.remote = remotes(root).get("origin", "")
        rep.tracked, rep.ignored = commit_set(root)
        # Scan what git would actually commit. The package plan's scan covers
        # the whole tree and is the stricter of the two; both must pass.
        rep.scan = scan_secrets(root, [root / r for r in rep.tracked])
    else:
        rep.problems.append("not a git repository — run `hackathon github init` first")
        rep.scan = plan.scan
    rep.problems.extend(validate_repo(root))
    if not plan.scan.passed:
        rep.problems.append(
            "secret scan of the working tree failed; nothing may be pushed until "
            "it passes (run `hackathon package --dry-run` for the detail)"
        )
    write_status(root, plan, github={
        "prepared": True, "branch": rep.branch, "remote": rep.remote,
        "ready": rep.ready, "problems": rep.problems,
        "commit_files": len(rep.tracked),
    })
    return rep


# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------


@dataclass
class PushReport:
    root: Path
    committed: bool = False
    pushed: bool = False
    remote: str = ""
    branch: str = ""
    message: str = ""
    output: str = ""

    def render(self) -> str:
        out = ["GITHUB PUSH", "-----------"]
        out.append(f"  commit   {'created' if self.committed else 'nothing to commit'}")
        out.append(f"  push     {'done' if self.pushed else 'not performed'}")
        if self.remote:
            out.append(f"  remote   {self.remote}  ({self.branch})")
        if self.message:
            out.append(f"  note     {self.message}")
        return "\n".join(out)


def push(
    root: Path,
    *,
    confirmed: bool = False,
    message: str = "",
    remote: str = "origin",
    branch: str = "",
    create: str = "",
    private: bool = True,
) -> PushReport:
    """Commit and push. Refuses unless `confirmed` is explicitly True.

    The confirmation is a parameter rather than a prompt inside this function
    so the CLI owns the interaction and the library stays callable from a test
    without a TTY -- but the default is False, so a caller that forgets has not
    accidentally published anything.
    """
    root = Path(root).resolve()
    rep = PushReport(root=root, remote=remote, branch=branch)

    if not confirmed:
        raise PushRefused(
            "push requires explicit confirmation. Nothing was sent. "
            "Re-run with --yes after reading `hackathon github prepare`."
        )
    if not is_repo(root):
        raise GitUnavailable("not a git repository — run `hackathon github init` first")

    prep = prepare(root)
    if not prep.plan.scan.passed or not (prep.scan and prep.scan.passed):
        raise PackageBlocked(prep.plan.scan.render())

    git(root, "add", "-A")
    status = git(root, "status", "--porcelain")
    if status.stdout.strip():
        msg = message or "Hackathon submission"
        out = git(root, "commit", "-m", msg)
        rep.committed = out.returncode == 0
        rep.output += out.stdout + out.stderr
        if not rep.committed:
            rep.message = f"commit failed: {out.stderr.strip()[:200]}"
            return rep

    rep.branch = branch or current_branch(root) or "main"

    if create and gh_available():
        vis = "--private" if private else "--public"
        out = subprocess.run(
            ["gh", "repo", "create", create, vis, "--source", str(root),
             "--remote", remote, "--push"],
            capture_output=True, text=True, timeout=300,
        )
        rep.output += out.stdout + out.stderr
        rep.pushed = out.returncode == 0
        if not rep.pushed:
            rep.message = f"gh repo create failed: {out.stderr.strip()[:200]}"
        return rep

    if remote not in remotes(root):
        rep.message = (
            f"no '{remote}' remote configured. Either add one "
            f"(git remote add {remote} <url>) or, with the GitHub CLI installed, "
            f"re-run with --create <owner>/<repo>."
        )
        return rep

    out = git(root, "push", "-u", remote, rep.branch)
    rep.output += out.stdout + out.stderr
    rep.pushed = out.returncode == 0
    if not rep.pushed:
        rep.message = f"push failed: {out.stderr.strip()[:300]}"
    write_status(root, None, github={
        "pushed": rep.pushed, "remote": remote, "branch": rep.branch,
    })
    return rep


def summary(root: Path) -> dict:
    """What `hackathon status` reports about GitHub readiness."""
    root = Path(root)
    st = read_status(root).get("github", {})
    st.setdefault("initialised", is_repo(root))
    st["gh_cli"] = gh_available()
    return st
