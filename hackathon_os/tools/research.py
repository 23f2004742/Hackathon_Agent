"""Research tools. Everything a research specialist learns must land in the
provenance ledger, so downstream claims stay traceable.

The ledger (RESEARCH/sources.json) is the mechanism behind two rules from the
brief: research keeps provenance (26), and the pitch invents no claims (22).
`verify_claims` lets the Final Auditor mechanically check the second.
"""

from __future__ import annotations

import html
import json
import re
import threading
from datetime import date
from pathlib import Path

from .base import approve, fail, guard, tool, truncate

LEDGER = "RESEARCH/sources.json"

# Research specialists run in parallel and all append here. Without this,
# concurrent read-modify-write silently loses sources.
_LEDGER_LOCK = threading.Lock()

# Anthropic-hosted. Runs on their servers, billed per use; there is no local
# function to implement. Handed only to specialists whose spec names it.
WEB_SEARCH_SERVER_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}


def _load(root: Path) -> list[dict]:
    p = root / LEDGER
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save(root: Path, rows: list[dict]) -> None:
    p = root / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=2), encoding="utf-8")


@tool("research", writes=True)
def record_source(
    claim: str,
    url: str = "",
    title: str = "",
    quote: str = "",
    confidence: str = "MEDIUM",
) -> str:
    """Record a factual claim and where it came from, into the provenance ledger.

    Call this for every substantive fact you intend to put in a report. A claim
    with no recorded source cannot be used by the pitch or presentation
    specialists, and the Final Auditor will flag it. If a figure is your own
    estimate rather than a sourced fact, record it with url="" and say so in the
    claim text -- an honest estimate is fine, an unmarked one is not.

    Args:
        claim: The specific factual statement, in one sentence.
        url: Where it came from. Leave empty for your own estimate or inference.
        title: Title of the source page or document.
        quote: A short verbatim excerpt supporting the claim, if you have one.
        confidence: HIGH, MEDIUM or LOW -- how much weight this should carry.
    """
    try:
        ctx = guard("record_source")
        with _LEDGER_LOCK:
            rows = _load(ctx.root)
            cid = f"S{len(rows) + 1:03d}"
            rows.append({
                "id": cid,
                "claim": claim.strip(),
                "url": url.strip(),
                "title": title.strip(),
                "quote": quote.strip()[:500],
                "confidence": confidence.upper(),
                "kind": "sourced" if url.strip() else "estimate",
                "recorded_by": ctx.agent,
                "recorded_on": date.today().isoformat(),
            })
            if not ctx.dry_run:
                _save(ctx.root, rows)
        return f"recorded {cid} ({'sourced' if url.strip() else 'ESTIMATE - unsourced'})"
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("research")
def list_sources(filter_text: str = "") -> str:
    """List recorded sources from the provenance ledger.

    Use this before researching something to check whether a teammate already
    established it -- repeating research wastes hackathon time and tokens.

    Args:
        filter_text: Only return entries whose claim or title contains this.
    """
    try:
        ctx = guard("list_sources")
        rows = _load(ctx.root)
        if filter_text:
            f = filter_text.lower()
            rows = [r for r in rows if f in r["claim"].lower() or f in r.get("title", "").lower()]
        if not rows:
            return "provenance ledger is empty" if not filter_text else f"no sources match '{filter_text}'"
        out = []
        for r in rows:
            mark = r["url"] or "NO SOURCE (estimate)"
            out.append(f"[{r['id']}] ({r['confidence']}) {r['claim']}\n      {mark}")
        return truncate("\n".join(out))
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("research")
def verify_claims(document: str) -> str:
    """Check a document's numeric and superlative claims against the ledger.

    Returns claims in the document that look factual but match no recorded
    source. Use this before shipping a pitch or report. It is a lint, not a
    proof: it catches unsourced numbers, not wrong ones.

    Args:
        document: Path to the markdown file to check, relative to project root.
    """
    try:
        ctx = guard("verify_claims")
        p = ctx.resolve(document)
        if not p.is_file():
            return f"no such file: {document}"
        text = p.read_text(encoding="utf-8", errors="replace")
        rows = _load(ctx.root)
        ledger_blob = " ".join(r["claim"] + " " + r.get("quote", "") for r in rows).lower()

        suspicious: list[str] = []
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith(("#", "|", "```", ">")):
                continue
            # Numbers with units/scale, percentages, currency, or superlatives.
            hits = re.findall(r"\$[\d,.]+[BMK]?|\b\d[\d,.]*\s?(?:%|billion|million|x)\b", s, re.I)
            sup = re.findall(r"\b(?:first|only|best|fastest|largest|no other|unique(?:ly)?)\b", s, re.I)
            if not hits and not sup:
                continue
            tokens = [h.lower().strip() for h in hits]
            if tokens and all(t in ledger_blob for t in tokens):
                continue
            suspicious.append(f"  line {i}: {s[:150]}")

        if not suspicious:
            return f"{document}: no unsourced factual claims detected ({len(rows)} sources in ledger)"
        return truncate(
            f"{document}: {len(suspicious)} claim(s) with no matching ledger entry.\n"
            "Either record_source them or soften the wording:\n" + "\n".join(suspicious)
        )
    except Exception as e:  # noqa: BLE001
        return fail(e)


@tool("web", approval=True)
def fetch_url(url: str, max_chars: int = 12000) -> str:
    """Fetch a web page or API response and return its text content.

    HTML is reduced to readable text. Use this to read a specific page you
    already know about; use web_search to discover pages.

    Args:
        url: Full URL including scheme.
        max_chars: Truncate the extracted text to this many characters.
    """
    try:
        ctx = guard("fetch_url")
        if not url.lower().startswith(("http://", "https://")):
            return "url must start with http:// or https://"
        if not approve(ctx, "fetch", url):
            return "User declined this fetch."
        if ctx.dry_run:
            return f"[dry-run] would fetch {url}"
        try:
            import requests
        except ImportError:
            return "requests is not installed; run: pip install requests"
        r = requests.get(url, timeout=30, headers={"User-Agent": "hackathon-os/1.0"})
        body = r.text
        if "html" in r.headers.get("content-type", "").lower():
            body = re.sub(r"(?is)<(script|style|nav|footer)[^>]*>.*?</\1>", " ", body)
            body = re.sub(r"(?s)<[^>]+>", " ", body)
            body = html.unescape(body)
            body = re.sub(r"[ \t]+", " ", body)
            body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
        ctx.note(f"fetch {url} -> {r.status_code}")
        return truncate(f"[{r.status_code}] {url}\n\n{body}", max_chars)
    except Exception as e:  # noqa: BLE001
        return fail(e)
