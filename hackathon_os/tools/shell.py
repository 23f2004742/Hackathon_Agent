"""Shell, test-running and log tools. Only engineering and delivery
specialists are given these."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

from .base import approve, fail, guard, tool, truncate

# Commands we refuse outright regardless of approval mode. This is a
# hackathon workstation, not a sandbox VM; a stray recursive delete or a
# force-push is not recoverable by "the user said yes at 3am".
FORBIDDEN = (
    "rm -rf /", "rm -rf ~", ":(){", "mkfs", "dd if=", "shutdown", "reboot",
    "git push --force", "git push -f", "chmod -R 777 /",
)


def _looks_destructive(cmd: str) -> str | None:
    low = " ".join(cmd.lower().split())
    for bad in FORBIDDEN:
        if bad in low:
            return bad
    return None


@tool("shell", writes=True, approval=True)
def run_shell(command: str, timeout_seconds: int = 120) -> str:
    """Run a shell command in the project root and return its output.

    Use this to install dependencies, run scripts, and inspect the environment.
    Returns exit code, stdout and stderr. A non-zero exit is returned to you as
    text, not raised -- read it and fix the cause.

    Args:
        command: The command line to run.
        timeout_seconds: Kill the command after this long. Default 120.
    """
    try:
        ctx = guard("run_shell")
        bad = _looks_destructive(command)
        if bad:
            return f"refused: command contains a destructive pattern ({bad!r})"
        if not approve(ctx, "shell", command):
            return "User declined this command."
        if ctx.dry_run:
            return f"[dry-run] would run: {command}"
        r = subprocess.run(
            command,
            shell=True,
            cwd=ctx.root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        ctx.note(f"shell rc={r.returncode}: {command[:80]}")
        out = f"[exit {r.returncode}]\n"
        if r.stdout:
            out += f"--- stdout ---\n{r.stdout}\n"
        if r.stderr:
            out += f"--- stderr ---\n{r.stderr}\n"
        return truncate(out)
    except subprocess.TimeoutExpired:
        return f"command timed out after {timeout_seconds}s"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("testing", writes=True, approval=True)
def run_tests(target: str = "tests", extra_args: str = "") -> str:
    """Run the project's test suite with pytest and return the result.

    Use this to verify your work actually runs. Never report a feature as
    working without running something that exercises it.

    Args:
        target: Test path or node id to run. Default "tests".
        extra_args: Additional pytest flags, e.g. "-k smoke -x".
    """
    try:
        ctx = guard("run_tests")
        cmd = f"{shlex.quote(sys.executable)} -m pytest {shlex.quote(target)} -q {extra_args}".strip()
        if not approve(ctx, "tests", cmd):
            return "User declined this test run."
        if ctx.dry_run:
            return f"[dry-run] would run: {cmd}"
        r = subprocess.run(
            cmd, shell=True, cwd=ctx.root, capture_output=True, text=True, timeout=600
        )
        ctx.note(f"tests rc={r.returncode} on {target}")
        return truncate(f"[exit {r.returncode}]\n{r.stdout}\n{r.stderr}")
    except subprocess.TimeoutExpired:
        return "test run timed out after 600s"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("testing")
def inspect_logs(path: str = "AGENT/logs", lines: int = 80) -> str:
    """Read the tail of a log file, or list the log directory.

    Args:
        path: Log file, or a directory to list. Default "AGENT/logs".
        lines: How many trailing lines to return. Default 80.
    """
    try:
        ctx = guard("inspect_logs")
        p = ctx.resolve(path)
        if p.is_dir():
            names = sorted(f.name for f in p.iterdir() if f.is_file())
            return "\n".join(names) if names else f"{path} is empty"
        if not p.is_file():
            return f"no such log: {path}"
        tail = p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        return truncate("\n".join(tail))
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("git")
def git_status() -> str:
    """Show the working tree status of the project's git repository, if any."""
    try:
        ctx = guard("git_status")
        if not (ctx.root / ".git").exists():
            return "not a git repository"
        r = subprocess.run(
            "git status --porcelain=v1 -b",
            shell=True, cwd=ctx.root, capture_output=True, text=True, timeout=30,
        )
        return truncate(r.stdout or "(clean)")
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("git")
def git_diff(path: str = "", staged: bool = False) -> str:
    """Show the diff of uncommitted changes.

    Args:
        path: Limit the diff to this path. Empty means the whole tree.
        staged: Show staged changes instead of unstaged. Default False.
    """
    try:
        ctx = guard("git_diff")
        if not (ctx.root / ".git").exists():
            return "not a git repository"
        cmd = "git diff" + (" --staged" if staged else "")
        if path:
            cmd += f" -- {shlex.quote(path)}"
        r = subprocess.run(
            cmd, shell=True, cwd=ctx.root, capture_output=True, text=True, timeout=30
        )
        return truncate(r.stdout or "(no changes)")
    except Exception as e:  # noqa: BLE001
        return fail(e)
