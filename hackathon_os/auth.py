"""Subscription authentication, and the hard refusal to spend money.

The OS runs on the operator's own Claude subscription. It does that the only
officially supported way: the Claude Agent SDK spawns the Claude Code CLI, and
the CLI uses whatever credential the operator already logged in with. No API
key is read, constructed, or asked for anywhere in this system.

Claude Code picks a credential by a documented precedence, most of which is
Console/API billing rather than a subscription:

  1. cloud provider    CLAUDE_CODE_USE_{BEDROCK,VERTEX,FOUNDRY,ANTHROPIC_AWS}
  2. bearer token      ANTHROPIC_AUTH_TOKEN
  3. API key           ANTHROPIC_API_KEY
  4. apiKeyHelper      a script named in settings.json
  5. long-lived OAuth  CLAUDE_CODE_OAUTH_TOKEN  (subscription; `claude setup-token`)
  6. Anthropic profile ANTHROPIC_PROFILE / federation env / active profile
  7. subscription      the OAuth login `claude` writes on /login

Only 5 and 7 are subscription-backed. So this module does two things:

  * `child_env()` neutralises 1-3 and 6 for the CLI subprocess, so a stray key
    in the operator's shell cannot silently redirect the run onto paid billing.
  * `probe()` refuses to start when the surviving credential is not one we can
    positively identify as a subscription. Nothing here falls back to an API
    key -- that is the whole point.

4 (apiKeyHelper) cannot be neutralised through the environment, so it is
detected and treated as a blocker rather than quietly worked around.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .glyphs import BOLD, GREEN, GREY, RED, RESET, YELLOW

# Escape hatch, deliberately awkward and off by default. Even when set, it only
# stops us from *scrubbing* paid credentials; it never selects one for you.
ALLOW_PAID_ENV = "HACKATHON_ALLOW_PAID_API"

# Credential sources that mean "someone is being billed per token".
PAID_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "ANTHROPIC_AWS_WORKSPACE_ID",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
    "ANTHROPIC_WORKSPACE_ID",
)

# Plans that include Claude Code. `claude setup-token` requires one of these.
SUBSCRIPTION_PLANS = ("pro", "max", "team", "enterprise")

SUBSCRIPTION_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"


class NoSubscriptionAuth(RuntimeError):
    """Raised instead of falling back to a paid credential."""


class UsageLimitReached(RuntimeError):
    """The plan's usage window is closed.

    Deliberately not a failed task: a failed task gets retried, and retrying
    against a closed window achieves nothing. This propagates all the way out
    so the run stops with work preserved, which is the only correct response
    when the alternative -- spending money -- is off the table.
    """


def _config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class AuthStatus:
    """What credential this machine would actually use, and whether we accept it."""

    ok: bool = False
    mechanism: str = "none"          # subscription_login | subscription_token | none
    plan: str = ""                   # pro | max | team | enterprise
    account: str = ""
    organization: str = ""
    cli_path: str = ""
    cli_version: str = ""
    sdk_version: str = ""
    overage_enabled: bool = False
    scrubbed: tuple[str, ...] = ()   # paid env vars we will hide from the CLI
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def billing(self) -> str:
        if self.mechanism.startswith("subscription"):
            plan = self.plan or "subscription"
            return f"Claude {plan} subscription (no per-token API charge)"
        return "none -- refusing to run"

    def render(self) -> str:
        mark = f"{GREEN}OK{RESET}" if self.ok else f"{RED}BLOCKED{RESET}"
        mech = {
            "subscription_login": "subscription OAuth login (claude /login)",
            "subscription_token": f"subscription OAuth token ({SUBSCRIPTION_TOKEN_ENV})",
            "none": "none found",
        }.get(self.mechanism, self.mechanism)
        lines = [
            f"  {BOLD}AUTHENTICATION{RESET}  {mark}",
            f"    mechanism   {mech}",
            f"    billing     {self.billing}",
        ]
        if self.account:
            org = f"  ({self.organization})" if self.organization else ""
            lines.append(f"    account     {self.account}{org}")
        if self.cli_version:
            lines.append(f"    claude cli  {self.cli_version}  {GREY}{self.cli_path}{RESET}")
        if self.sdk_version:
            lines.append(f"    agent sdk   claude-agent-sdk {self.sdk_version}")
        if self.scrubbed:
            lines.append(
                f"    {YELLOW}hidden{RESET}      {', '.join(self.scrubbed)} "
                f"{GREY}(paid credential; not passed to the CLI){RESET}"
            )
        for w in self.warnings:
            lines.append(f"    {YELLOW}warning{RESET}     {w}")
        for b in self.blockers:
            lines.append(f"    {RED}blocker{RESET}     {b}")
        return "\n".join(lines)


def _cli() -> tuple[str, str]:
    """Locate the Claude Code CLI and read its version. Empty strings if absent."""
    path = shutil.which("claude") or ""
    if not path:
        return "", ""
    try:
        out = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return path, ""
    first = out.stdout.strip().splitlines()
    return path, first[0] if first else ""


def _stored_login() -> tuple[str, dict]:
    """Read the subscription plan and account from Claude Code's own credential store.

    On macOS the credential may live in the Keychain rather than on disk, so an
    absent file is not proof of an absent login -- the account record in
    `~/.claude.json` is the fallback signal.
    """
    creds = _read_json(_config_dir() / ".credentials.json").get("claudeAiOauth") or {}
    account = _read_json(Path.home() / ".claude.json").get("oauthAccount") or {}
    plan = str(creds.get("subscriptionType") or "").lower()
    if not plan:
        org_type = str(account.get("organizationType") or "")
        billing = str(account.get("billingType") or "")
        if org_type.startswith("claude_") and "subscription" in billing:
            plan = org_type[len("claude_"):]
    return plan, account


def _api_key_helper() -> str | None:
    """apiKeyHelper outranks the subscription login and cannot be unset by env."""
    for path in (
        _config_dir() / "settings.json",
        Path.cwd() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.local.json",
    ):
        helper = _read_json(path).get("apiKeyHelper")
        if helper:
            return f"{path}: apiKeyHelper={helper}"
    return None


def scrubbable() -> tuple[str, ...]:
    """Paid credential env vars currently set in this process."""
    return tuple(v for v in PAID_ENV_VARS if os.environ.get(v))


def child_env() -> dict[str, str]:
    """Environment overrides for the CLI subprocess.

    Empty-string values are how we unset: the SDK merges this dict over the
    inherited environment, and Claude Code treats an empty credential var as
    absent. The result is a child process that cannot reach paid billing even
    when the parent shell is configured for it.
    """
    if os.environ.get(ALLOW_PAID_ENV) == "1":
        return {}
    return {v: "" for v in scrubbable()}


def probe(*, check_cli: bool = True) -> AuthStatus:
    """Determine whether we may run, without making a single model request."""
    status = AuthStatus(scrubbed=scrubbable())

    try:
        import claude_agent_sdk
        status.sdk_version = getattr(claude_agent_sdk, "__version__", "installed")
    except ImportError:
        status.blockers.append(
            "claude-agent-sdk is not installed. Run: pip install claude-agent-sdk"
        )

    if check_cli:
        status.cli_path, status.cli_version = _cli()
        if not status.cli_path and not status.sdk_version:
            status.blockers.append(
                "no Claude Code CLI on PATH and no SDK-bundled binary. "
                "Install it, then run `claude` once to log in."
            )

    helper = _api_key_helper()
    if helper and os.environ.get(ALLOW_PAID_ENV) != "1":
        status.blockers.append(
            f"apiKeyHelper is configured and outranks your subscription login "
            f"({helper}). Remove it, or this run could be billed to an API key."
        )

    # A long-lived subscription token is explicitly subscription-backed and is
    # the supported path for unattended runs, so it wins when present.
    if os.environ.get(SUBSCRIPTION_TOKEN_ENV):
        status.mechanism = "subscription_token"
    else:
        plan, account = _stored_login()
        if plan in SUBSCRIPTION_PLANS:
            status.mechanism = "subscription_login"
            status.plan = plan
            status.account = str(account.get("emailAddress") or "")
            status.organization = str(account.get("organizationName") or "")
            status.overage_enabled = bool(account.get("hasExtraUsageEnabled"))
            if status.overage_enabled:
                status.warnings.append(
                    "extra usage (paid overage) is enabled on this account. The OS "
                    "never opts in, but Anthropic may bill overage past your plan "
                    "limit. Turn it off in Claude settings to be certain."
                )
        elif plan:
            status.blockers.append(
                f"the logged-in Claude account is on plan '{plan}', which does not "
                f"include Claude Code. Needs one of: {', '.join(SUBSCRIPTION_PLANS)}."
            )
        else:
            status.blockers.append(
                "no Claude subscription login found. Run `claude` once and log in, "
                f"or set {SUBSCRIPTION_TOKEN_ENV} from `claude setup-token`."
            )

    status.ok = not status.blockers and status.mechanism != "none"
    return status


def require() -> AuthStatus:
    """Probe, and refuse to continue rather than reaching for a paid credential."""
    status = probe()
    if not status.ok:
        raise NoSubscriptionAuth(
            "subscription authentication is unavailable:\n  - "
            + "\n  - ".join(status.blockers)
            + "\n\nThis system has no paid-API fallback by design. "
            "Fix the above and re-run; `python hackathon.py auth` explains how."
        )
    return status
