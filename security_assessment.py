"""Routine security-posture checks across the systems Deepiri depends on:
GitHub org access + security alerts, Discord server admins, Plaky access
(new users + admins), Deepiri.com (TLS + security headers), email domain
(SPF/DMARC), and Deepiri Cloud (pluggable via env -- no cloud API wired in
yet, so it reports "not configured" until one is).

Each check returns a plain dict: {"status", "summary", "details"} where
status is one of "ok" | "warning" | "critical" | "unknown". Kept free of any
discord.py imports so it can be unit tested without a live bot/guild -- the
Discord check just duck-types on `guild.members` / `member.guild_permissions`.
"""

import logging
import os
import socket
import ssl
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("deepiri.security_assessment")

GITHUB_API_BASE = "https://api.github.com"
DNS_OVER_HTTPS_URL = "https://dns.google/resolve"


def _finding(status: str, summary: str, details: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"status": status, "summary": summary, "details": details or []}


def _gh_headers(github_pat: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ---------------------------------------------------------------------------
# GitHub: org admins + org-level security alerts
# ---------------------------------------------------------------------------

def check_github_admins(org: str, github_pat: str) -> Dict[str, Any]:
    """Who currently holds org-owner ('admin' role) on GitHub -- surfaces
    unexpected/forgotten owners rather than trusting institutional memory."""
    if not org or not github_pat:
        return _finding("unknown", "GITHUB_ORG or GITHUB_PAT not configured.")
    try:
        response = requests.get(
            f"{GITHUB_API_BASE}/orgs/{org}/members",
            headers=_gh_headers(github_pat),
            params={"role": "admin", "per_page": 100},
            timeout=20,
        )
    except requests.RequestException:
        logger.exception("check_github_admins: request failed")
        return _finding("unknown", "Could not reach the GitHub API to list org admins.")
    if response.status_code != 200:
        return _finding("unknown", f"GitHub org-admin lookup failed ({response.status_code}).")
    admins = sorted(u.get("login", "?") for u in response.json())
    return _finding(
        "ok" if admins else "warning",
        f"{len(admins)} org owner(s): {', '.join(admins) if admins else 'none found'}",
        admins,
    )


def check_github_security_alerts(org: str, github_pat: str) -> Dict[str, Any]:
    """Org-level Dependabot + secret-scanning alerts still open. These
    endpoints require GitHub Advanced Security / org admin token scope --
    a 403/404 means the token can't see them, reported as unknown rather
    than silently treated as "no alerts"."""
    if not org or not github_pat:
        return _finding("unknown", "GITHUB_ORG or GITHUB_PAT not configured.")

    headers = _gh_headers(github_pat)
    details: List[str] = []
    open_count = 0
    saw_any_access = False

    for label, path in (
        ("Dependabot", f"/orgs/{org}/dependabot/alerts"),
        ("Secret scanning", f"/orgs/{org}/secret-scanning/alerts"),
    ):
        try:
            response = requests.get(
                f"{GITHUB_API_BASE}{path}",
                headers=headers,
                params={"state": "open", "per_page": 100},
                timeout=20,
            )
        except requests.RequestException:
            logger.exception("check_github_security_alerts: %s request failed", label)
            details.append(f"{label}: request failed")
            continue
        if response.status_code == 200:
            saw_any_access = True
            alerts = response.json()
            open_count += len(alerts)
            details.append(f"{label}: {len(alerts)} open alert(s)")
        elif response.status_code in (403, 404):
            details.append(f"{label}: not accessible with current token/plan ({response.status_code})")
        else:
            details.append(f"{label}: lookup failed ({response.status_code})")

    if not saw_any_access:
        return _finding("unknown", "Could not access GitHub security-alert endpoints for this org.", details)
    status = "critical" if open_count > 0 else "ok"
    return _finding(status, f"{open_count} open security alert(s) across the org.", details)


# ---------------------------------------------------------------------------
# Discord: who holds server-administrator permission
# ---------------------------------------------------------------------------

def check_discord_admins(guild: Any) -> Dict[str, Any]:
    """Members holding the Administrator permission on the guild -- the
    Discord-side equivalent of the GitHub org-owner check."""
    if guild is None:
        return _finding("unknown", "Discord guild not resolved yet.")
    admins = []
    for member in getattr(guild, "members", []):
        if getattr(member, "bot", False):
            continue
        perms = getattr(member, "guild_permissions", None)
        if perms is not None and getattr(perms, "administrator", False):
            admins.append(str(getattr(member, "display_name", member)))
    admins.sort()
    return _finding(
        "ok" if admins else "warning",
        f"{len(admins)} Discord administrator(s): {', '.join(admins) if admins else 'none found'}",
        admins,
    )


# ---------------------------------------------------------------------------
# Plaky: new users joined recently + who holds admin
# ---------------------------------------------------------------------------

def _plaky_user_role(user: Dict[str, Any]) -> str:
    for key in ("role", "userRole", "accessLevel"):
        v = user.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    if user.get("isAdmin") is True or user.get("admin") is True:
        return "admin"
    return ""


def _plaky_user_joined_at(user: Dict[str, Any]) -> Optional[datetime]:
    for key in ("createdAt", "joinedAt", "createdOn", "dateJoined"):
        raw = user.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def check_plaky_access(api_key: str, since_days: int = 7) -> Dict[str, Any]:
    """New Plaky accounts created in the last `since_days` days, plus who
    currently holds an admin/owner role. Best-effort: Plaky's public API
    doesn't guarantee consistent field names across accounts, so an unknown
    join date or role is reported rather than assumed."""
    if not api_key:
        return _finding("unknown", "PLAKY_API_KEY not configured.")

    from plaky import list_plaky_users  # local import: avoid a hard dependency at module load time

    users = list_plaky_users(api_key)
    if not users:
        return _finding("unknown", "Could not fetch the Plaky user roster.")

    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    new_users = []
    admins = []
    for user in users:
        name = str(user.get("name") or user.get("displayName") or user.get("username") or "unknown")
        joined = _plaky_user_joined_at(user)
        if joined is not None and joined >= cutoff:
            new_users.append(name)
        if _plaky_user_role(user) in ("admin", "owner"):
            admins.append(name)

    new_users.sort()
    admins.sort()
    details = [f"New in last {since_days}d: {', '.join(new_users) if new_users else 'none'}", f"Admins: {', '.join(admins) if admins else 'none found'}"]
    status = "warning" if new_users else "ok"
    return _finding(status, f"{len(new_users)} new Plaky user(s) in the last {since_days} days, {len(admins)} admin(s).", details)


# ---------------------------------------------------------------------------
# Deepiri.com: TLS certificate expiry + baseline security headers
# ---------------------------------------------------------------------------

def check_website_security(url: str, *, cert_warn_days: int = 21) -> Dict[str, Any]:
    """TLS cert expiry (via a real handshake, stdlib ssl -- no extra deps or
    API key needed) plus presence of baseline security response headers.
    Not a WAF/malicious-request scan -- that needs a real log source; wire
    WEBSITE_SECURITY_LOG_API_URL in once one exists."""
    if not url:
        return _finding("unknown", "Website URL not configured.")
    host = url.split("://", 1)[-1].split("/", 1)[0]
    details: List[str] = []
    status = "ok"

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
        not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days_left = (not_after - datetime.now(timezone.utc)).days
        if days_left < 0:
            status = "critical"
            details.append(f"TLS certificate EXPIRED {abs(days_left)} day(s) ago")
        elif days_left <= cert_warn_days:
            status = "warning"
            details.append(f"TLS certificate expires in {days_left} day(s)")
        else:
            details.append(f"TLS certificate valid for {days_left} more day(s)")
    except Exception:
        logger.exception("check_website_security: TLS check failed for %s", host)
        details.append("Could not complete a TLS handshake")
        status = "unknown"

    try:
        response = requests.get(f"https://{host}", timeout=15, allow_redirects=True)
        missing = [h for h in ("Strict-Transport-Security", "X-Content-Type-Options", "X-Frame-Options") if h not in response.headers]
        if missing:
            details.append(f"Missing security headers: {', '.join(missing)}")
            if status == "ok":
                status = "warning"
        else:
            details.append("Baseline security headers present")
    except requests.RequestException:
        logger.exception("check_website_security: header check failed for %s", host)
        details.append("Could not fetch response headers")
        if status == "ok":
            status = "unknown"

    log_api_url = (os.getenv("WEBSITE_SECURITY_LOG_API_URL") or "").strip()
    if not log_api_url:
        details.append("Malicious-request/WAF log scanning not configured (set WEBSITE_SECURITY_LOG_API_URL)")

    return _finding(status, f"{host}: " + "; ".join(details[:2]), details)


# ---------------------------------------------------------------------------
# Email domain: SPF / DMARC presence via DNS-over-HTTPS (no API key needed)
# ---------------------------------------------------------------------------

def _dns_txt_records(name: str) -> List[str]:
    try:
        response = requests.get(DNS_OVER_HTTPS_URL, params={"name": name, "type": "TXT"}, timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        logger.exception("_dns_txt_records: lookup failed for %s", name)
        return []
    return [a.get("data", "").strip('"') for a in payload.get("Answer", []) if a.get("data")]


def check_email_security(domain: str) -> Dict[str, Any]:
    """SPF and DMARC record presence for the mail domain -- missing either
    is a real spoofing exposure, cheap to check via public DNS, no
    credentials required."""
    if not domain:
        return _finding("unknown", "Email domain not configured.")

    spf_records = [r for r in _dns_txt_records(domain) if r.lower().startswith("v=spf1")]
    dmarc_records = [r for r in _dns_txt_records(f"_dmarc.{domain}") if r.lower().startswith("v=dmarc1")]

    details = [
        f"SPF: {'present' if spf_records else 'MISSING'}",
        f"DMARC: {'present' if dmarc_records else 'MISSING'}",
    ]
    if not spf_records or not dmarc_records:
        return _finding("critical", f"{domain}: missing " + " and ".join(
            [n for n, present in (("SPF", spf_records), ("DMARC", dmarc_records)) if not present]
        ), details)
    return _finding("ok", f"{domain}: SPF and DMARC both present.", details)


# ---------------------------------------------------------------------------
# Deepiri Cloud: pluggable posture check (no vendor wired in yet)
# ---------------------------------------------------------------------------

def check_deepiri_cloud() -> Dict[str, Any]:
    """Placeholder until a real cloud security-posture source (AWS Security
    Hub, GCP SCC, a custom endpoint, ...) is wired in. If
    CLOUD_SECURITY_STATUS_URL is set, does a best-effort GET and expects
    {"status": "ok"|"warning"|"critical", "summary": str, "details": [...]}."""
    status_url = (os.getenv("CLOUD_SECURITY_STATUS_URL") or "").strip()
    if not status_url:
        return _finding("unknown", "Deepiri Cloud security-posture check not configured (set CLOUD_SECURITY_STATUS_URL).")
    headers = {}
    api_key = (os.getenv("CLOUD_SECURITY_STATUS_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.get(status_url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        logger.exception("check_deepiri_cloud: status endpoint request failed")
        return _finding("unknown", "Could not reach the configured Deepiri Cloud security-status endpoint.")
    except ValueError:
        return _finding("unknown", "Deepiri Cloud security-status endpoint returned a non-JSON response.")
    status = str(payload.get("status") or "unknown").lower()
    if status not in ("ok", "warning", "critical"):
        status = "unknown"
    return _finding(status, str(payload.get("summary") or "No summary provided."), payload.get("details") or [])


# ---------------------------------------------------------------------------
# Orchestration + rendering
# ---------------------------------------------------------------------------

CHECK_ORDER = ["github_admins", "github_alerts", "discord_admins", "plaky_access", "website", "email", "cloud"]

CHECK_LABELS = {
    "github_admins": "GitHub org admins",
    "github_alerts": "GitHub security alerts",
    "discord_admins": "Discord admins",
    "plaky_access": "Plaky access",
    "website": "Deepiri.com",
    "email": "Email (SPF/DMARC)",
    "cloud": "Deepiri Cloud",
}

STATUS_EMOJI = {"ok": "✅", "warning": "⚠️", "critical": "🚨", "unknown": "❔"}


def run_full_assessment(
    *,
    github_org: str,
    github_pat: str,
    plaky_api_key: str,
    guild: Any,
    website_url: str,
    email_domain: str,
) -> Dict[str, Dict[str, Any]]:
    """Runs every check synchronously -- callers on an async bot should wrap
    this in asyncio.to_thread, same pattern as the other blocking API calls
    in this codebase (github.py, plaky.py)."""
    return {
        "github_admins": check_github_admins(github_org, github_pat),
        "github_alerts": check_github_security_alerts(github_org, github_pat),
        "discord_admins": check_discord_admins(guild),
        "plaky_access": check_plaky_access(plaky_api_key),
        "website": check_website_security(website_url),
        "email": check_email_security(email_domain),
        "cloud": check_deepiri_cloud(),
    }


def overall_status(results: Dict[str, Dict[str, Any]]) -> str:
    statuses = {r["status"] for r in results.values()}
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses:
        return "warning"
    if statuses == {"ok"}:
        return "ok"
    return "unknown"


def render_digest_lines(results: Dict[str, Dict[str, Any]]) -> List[str]:
    """One line per category -- for the twice-weekly channel digest and the
    weekly DM, both of which should be skimmable in a few seconds."""
    lines = []
    for key in CHECK_ORDER:
        result = results.get(key)
        if result is None:
            continue
        emoji = STATUS_EMOJI.get(result["status"], "❔")
        lines.append(f"{emoji} **{CHECK_LABELS[key]}**: {result['summary']}")
    return lines


def render_indepth_report(results: Dict[str, Dict[str, Any]]) -> List[str]:
    """Full detail, one block per category -- for the on-demand in-depth
    command."""
    blocks = []
    for key in CHECK_ORDER:
        result = results.get(key)
        if result is None:
            continue
        emoji = STATUS_EMOJI.get(result["status"], "❔")
        lines = [f"{emoji} **{CHECK_LABELS[key]}** — {result['summary']}"]
        for detail in result.get("details", []):
            lines.append(f"   • {detail}")
        blocks.append("\n".join(lines))
    return blocks
