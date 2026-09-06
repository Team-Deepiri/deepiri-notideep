"""Unit tests for security_assessment.py -- each check is exercised in
isolation via monkeypatched requests/plaky calls, plus the digest/in-depth
rendering and overall-status rollup."""

from types import SimpleNamespace
from unittest.mock import Mock

import security_assessment as sa


def _resp(status_code=200, json_data=None, headers=None):
    r = Mock()
    r.status_code = status_code
    r.headers = headers or {}
    r.json = Mock(return_value=json_data if json_data is not None else [])
    r.raise_for_status = Mock()
    if status_code >= 400:
        import requests
        r.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    return r


def test_check_github_admins_missing_config():
    result = sa.check_github_admins("", "")
    assert result["status"] == "unknown"


def test_check_github_admins_lists_owners(monkeypatch):
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _resp(200, [{"login": "alice"}, {"login": "bob"}]))
    result = sa.check_github_admins("Team-Deepiri", "fake-pat")
    assert result["status"] == "ok"
    assert result["details"] == ["alice", "bob"]


def test_check_github_admins_no_owners_is_warning(monkeypatch):
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _resp(200, []))
    result = sa.check_github_admins("Team-Deepiri", "fake-pat")
    assert result["status"] == "warning"


def test_check_github_security_alerts_counts_open(monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=None):
        if "dependabot" in url:
            return _resp(200, [{"id": 1}])
        return _resp(200, [])
    monkeypatch.setattr(sa.requests, "get", fake_get)
    result = sa.check_github_security_alerts("Team-Deepiri", "fake-pat")
    assert result["status"] == "critical"
    assert "1 open security alert" in result["summary"]


def test_check_github_security_alerts_none_open_is_ok(monkeypatch):
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _resp(200, []))
    result = sa.check_github_security_alerts("Team-Deepiri", "fake-pat")
    assert result["status"] == "ok"


def test_check_github_security_alerts_no_access_is_unknown(monkeypatch):
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _resp(403))
    result = sa.check_github_security_alerts("Team-Deepiri", "fake-pat")
    assert result["status"] == "unknown"


def test_check_discord_admins_finds_administrator_permission():
    admin_member = SimpleNamespace(bot=False, display_name="AdminPerson", guild_permissions=SimpleNamespace(administrator=True))
    regular_member = SimpleNamespace(bot=False, display_name="RegularPerson", guild_permissions=SimpleNamespace(administrator=False))
    bot_member = SimpleNamespace(bot=True, display_name="SomeBot", guild_permissions=SimpleNamespace(administrator=True))
    guild = SimpleNamespace(members=[admin_member, regular_member, bot_member])

    result = sa.check_discord_admins(guild)

    assert result["status"] == "ok"
    assert result["details"] == ["AdminPerson"]


def test_check_discord_admins_no_admins_is_warning():
    member = SimpleNamespace(bot=False, display_name="Nobody", guild_permissions=SimpleNamespace(administrator=False))
    guild = SimpleNamespace(members=[member])
    result = sa.check_discord_admins(guild)
    assert result["status"] == "warning"


def test_check_discord_admins_no_guild_is_unknown():
    result = sa.check_discord_admins(None)
    assert result["status"] == "unknown"


def test_check_plaky_access_flags_new_users_and_admins(monkeypatch):
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    users = [
        {"name": "New Person", "createdAt": recent, "role": "member"},
        {"name": "Old Admin", "createdAt": old, "isAdmin": True},
    ]
    monkeypatch.setattr("plaky.list_plaky_users", lambda api_key: users)

    result = sa.check_plaky_access("fake-key", since_days=7)

    assert result["status"] == "warning"
    assert "New Person" in result["details"][0]
    assert "Old Admin" in result["details"][1]


def test_check_plaky_access_missing_key():
    result = sa.check_plaky_access("")
    assert result["status"] == "unknown"


def test_check_email_security_both_present(monkeypatch):
    def fake_dns(name):
        if name.startswith("_dmarc"):
            return ["v=DMARC1; p=reject"]
        return ["v=spf1 include:_spf.google.com ~all"]
    monkeypatch.setattr(sa, "_dns_txt_records", fake_dns)
    result = sa.check_email_security("deepiri.com")
    assert result["status"] == "ok"


def test_check_email_security_missing_dmarc_is_critical(monkeypatch):
    def fake_dns(name):
        if name.startswith("_dmarc"):
            return []
        return ["v=spf1 ~all"]
    monkeypatch.setattr(sa, "_dns_txt_records", fake_dns)
    result = sa.check_email_security("deepiri.com")
    assert result["status"] == "critical"
    assert "DMARC" in result["summary"]


def test_check_deepiri_cloud_not_configured(monkeypatch):
    monkeypatch.delenv("CLOUD_SECURITY_STATUS_URL", raising=False)
    result = sa.check_deepiri_cloud()
    assert result["status"] == "unknown"


def test_check_deepiri_cloud_uses_configured_endpoint(monkeypatch):
    monkeypatch.setenv("CLOUD_SECURITY_STATUS_URL", "https://cloud.example/status")
    monkeypatch.setattr(sa.requests, "get", lambda *a, **k: _resp(200, {"status": "warning", "summary": "1 stale IAM key", "details": ["key-123"]}))
    result = sa.check_deepiri_cloud()
    assert result["status"] == "warning"
    assert result["summary"] == "1 stale IAM key"


def test_overall_status_rolls_up_worst_case():
    results = {
        "a": {"status": "ok"}, "b": {"status": "warning"}, "c": {"status": "critical"},
    }
    assert sa.overall_status(results) == "critical"
    assert sa.overall_status({"a": {"status": "ok"}, "b": {"status": "warning"}}) == "warning"
    assert sa.overall_status({"a": {"status": "ok"}}) == "ok"


def test_render_digest_and_indepth_cover_all_checks():
    results = {k: sa._finding("ok", f"{k} fine") for k in sa.CHECK_ORDER}
    digest = sa.render_digest_lines(results)
    indepth = sa.render_indepth_report(results)
    assert len(digest) == len(sa.CHECK_ORDER)
    assert len(indepth) == len(sa.CHECK_ORDER)
