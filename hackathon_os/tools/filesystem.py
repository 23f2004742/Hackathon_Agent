"""Filesystem tools. Every path is scoped to the project; writes are also
scoped to the calling specialist's declared write_paths."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from .base import approve, fail, guard, tool, truncate

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", "dist", "build"}


@tool("filesystem")
def list_files(pattern: str = "**/*", max_results: int = 200) -> str:
    """List files in the project matching a glob pattern.

    Use this to find your bearings before reading or writing anything.

    Args:
        pattern: Glob relative to the project root, e.g. "RESEARCH/*.md" or
            "src/**/*.py". Default "**/*" lists everything.
        max_results: Cap on returned paths. Default 200.
    """
    try:
        ctx = guard("list_files")
        hits: list[str] = []
        for p in sorted(ctx.root.glob(pattern)):
            if not p.is_file():
                continue
            rel = p.relative_to(ctx.root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            hits.append(f"{rel.as_posix()}  ({p.stat().st_size}B)")
            if len(hits) >= max_results:
                break
        return "\n".join(hits) if hits else f"no files match '{pattern}'"
    except Exception as e:  # noqa: BLE001 - tools return errors, never raise
        return fail(e)


@tool("filesystem")
def read_file(path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a text file from the project.

    Read before you write. If you only need part of a large file, pass a line
    range rather than pulling the whole thing into context.

    Args:
        path: Path relative to the project root.
        start_line: 1-indexed first line to return. Default 1.
        end_line: Last line to return; 0 means to end of file. Default 0.
    """
    try:
        ctx = guard("read_file")
        p = ctx.resolve(path)
        if not p.is_file():
            return f"no such file: {path}"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        lo = max(1, start_line) - 1
        hi = len(lines) if end_line <= 0 else min(len(lines), end_line)
        body = "\n".join(lines[lo:hi])
        header = f"[{path} lines {lo + 1}-{hi} of {len(lines)}]\n"
        return truncate(header + body)
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("filesystem", writes=True, approval=True)
def write_file(path: str, content: str) -> str:
    """Create or overwrite a file in the project.

    Only paths inside your specialist's write scope are permitted; the error
    will tell you your scope if you are outside it. Overwrites replace the file
    wholesale -- use edit_file for a targeted change.

    Args:
        path: Path relative to the project root.
        content: Full file contents to write.
    """
    try:
        ctx = guard("write_file")
        p = ctx.resolve_for_write(path)
        existed = p.is_file()
        verb = "overwrite" if existed else "create"
        if not approve(ctx, verb, path):
            return "User declined this write."
        if ctx.dry_run:
            return f"[dry-run] would {verb} {path} ({len(content)}B)"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        ctx.note(f"{verb} {path} ({len(content)}B)")
        return f"{verb}d {path} ({len(content)} bytes)"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("filesystem", writes=True, approval=True)
def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace an exact substring in a file, leaving the rest untouched.

    Prefer this over write_file when changing part of an existing file. The
    match must be unique; if it is not, include more surrounding context.

    Args:
        path: Path relative to the project root.
        old_text: Exact text to find. Must appear exactly once.
        new_text: Text to replace it with.
    """
    try:
        ctx = guard("edit_file")
        p = ctx.resolve_for_write(path)
        if not p.is_file():
            return f"no such file: {path}"
        body = p.read_text(encoding="utf-8")
        n = body.count(old_text)
        if n == 0:
            return f"old_text not found in {path}"
        if n > 1:
            return f"old_text appears {n} times in {path}; include more context to disambiguate"
        if not approve(ctx, "edit", path):
            return "User declined this edit."
        if ctx.dry_run:
            return f"[dry-run] would edit {path}"
        p.write_text(body.replace(old_text, new_text), encoding="utf-8")
        ctx.note(f"edit {path}")
        return f"edited {path}"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("code")
def search_code(query: str, glob: str = "**/*", max_results: int = 60) -> str:
    """Search file contents for a literal substring, returning path:line matches.

    Use this to locate an implementation before changing it, or to check whether
    something already exists before building it.

    Args:
        query: Literal text to find. Case-sensitive.
        glob: Restrict to files matching this glob. Default all files.
        max_results: Cap on matching lines returned. Default 60.
    """
    try:
        ctx = guard("search_code")
        hits: list[str] = []
        for p in sorted(ctx.root.glob(glob)):
            if not p.is_file():
                continue
            rel = p.relative_to(ctx.root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if query not in text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if query in line:
                    hits.append(f"{rel.as_posix()}:{i}: {line.strip()[:160]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + "\n[result cap reached]"
        return "\n".join(hits) if hits else f"no matches for '{query}'"
    except Exception as e:  # noqa: BLE001
        return fail(e)
