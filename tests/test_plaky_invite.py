"""Plaky invite pipeline: support-ticket 'add to plaky' intent parsing, the
ordered email-resolution chain (local -> cloud DB -> GitHub -> Plaky roster),
in-thread email asks, and routing invites through the headless bridge."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import main
import plaky_invite


@pytest.fixture(autouse=True)
def _user_data_tmp(monkeypatch, tmp_path):
    """Keep every test's identity writes out of the repo working tree."""
    monkeypatch.setattr(plaky_invite, "USER_DATA_PATH", tmp_path / "user_data.json")


def _member(discord_id: int = 42, name: str = "jane"):
    m = Mock(spec=__import__("discord").Member)
    m.id = discord_id
    m.display_name = name
    m.global_name = name
    m.name = name
    m.bot = False
    m.get_role.return_value = None
    m.add_roles = AsyncMock()
    m.remove_roles = AsyncMock()
    return m


def _ticket_channel(thread_id: int, parent_id: int = 100):
    channel = SimpleNamespace(
        id=thread_id,
        parent_id=parent_id,
        name="ticket",
        get_thread=lambda mid: None,
        fetch_message=AsyncMock(return_value=SimpleNamespace(thread=None)),
        send=AsyncMock(),
    )
    return channel


def _ticket_message(member, channel, content: str):
    return SimpleNamespace(
        id=999,
        guild=SimpleNamespace(),
        channel=channel,
        thread=None,
        content=content,
        author=member,
        mentions=[],
    )


@pytest.mark.parametrize(
    "text",
    [
        "I want to add someone to the Plaky",
        "i need to be added to plaky",
        "i needed added to plaky",
        "add me to the plaky please",
        "please invite me to plaky",
        "can you add claire to plaky?",
        "I signed the IPCA, please add me to plaky",
    ],
)
def test_is_plaky_add_intent_matches_request_forms(text):
    assert main._is_plaky_add_intent(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "remove me from plaky",
        "i need to be removed from plaky",
        "offboard user x from the plaky",
        "the plaky invite worked",
        "plaky invite was sent already",
        "plaky status all good",
    ],
)
def test_is_plaky_add_intent_rejects_removal_and_report_noise(text):
    assert main._is_plaky_add_intent(text) is False


def test_remember_user_data_never_overwrites_with_none(monkeypatch, tmp_path):
    from plaky_invite import remember_user_data, get_user_data

    monkeypatch.setattr(plaky_invite, "USER_DATA_PATH", tmp_path / "user_data.json")
    remember_user_data(1, email="a@deepiri.com")
    assert get_user_data(1)["email"] == "a@deepiri.com"
    remember_user_data(1, email=None, github_username="jane")
    assert get_user_data(1)["email"] == "a@deepiri.com"
    assert get_user_data(1)["github_username"] == "jane"


@pytest.mark.asyncio
async def test_call_plaky_bridge_invite_contract(monkeypatch):
    sent = {}

    class FakeResponse:
        status_code = 200
        content = b'{"success": true, "via": "cake"}'

        def json(self):
            return {"success": True, "via": "cake"}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            sent["url"] = url
            sent["json"] = json
            sent["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(plaky_invite, "PLAKY_BRIDGE_URL", "http://bridge:5009")
    monkeypatch.setattr(plaky_invite, "INTERNAL_SERVICE_SECRET", "secret-123")
    monkeypatch.setattr(plaky_invite.httpx, "AsyncClient", FakeClient)

    result = await plaky_invite.call_plaky_bridge_invite("jane@deepiri.com")

    assert result["success"] is True
    assert result["via"] == "cake"
    assert sent["url"] == "http://bridge:5009/plaky/invite"
    assert sent["json"] == {"email": "jane@deepiri.com", "role": "MEMBER"}
    assert sent["headers"]["X-Internal-Secret"] == "secret-123"


@pytest.mark.asyncio
async def test_invite_uses_explicit_email_and_persists(monkeypatch):
    monkeypatch.setattr(main, "PLAKY_API_KEY", "pk")
    bridge_invite = AsyncMock(return_value={"success": True, "via": "cake"})
    monkeypatch.setattr(main, "call_plaky_bridge_invite", bridge_invite)
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "resolve_member_email", resolve)

    status, email = await main._invite_member_to_plaky(
        discord_id=42,
        discord_username="jane",
        email="jane@deepiri.com",
    )

    assert status == "ok"
    assert email == "jane@deepiri.com"
    bridge_invite.assert_awaited_once_with("jane@deepiri.com", role="MEMBER")
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_invite_asks_when_email_unresolved(monkeypatch):
    monkeypatch.setattr(main, "PLAKY_API_KEY", "pk")
    monkeypatch.setattr(main, "call_plaky_bridge_invite", AsyncMock())
    monkeypatch.setattr(main, "resolve_member_email", AsyncMock(return_value=None))

    status, email = await main._invite_member_to_plaky(discord_id=42, discord_username="jane")

    assert status == "asked"
    assert email is None


@pytest.mark.asyncio
async def test_add_request_self_asks_for_email_in_thread(monkeypatch):
    member = _member()
    channel = _ticket_channel(thread_id=555)
    message = _ticket_message(member, channel, "i need to be added to plaky")
    monkeypatch.setattr(main, "SUPPORT_SESSIONS_CHANNEL_ID", 100)
    monkeypatch.setattr(main, "GITHUB_PROFILES_CHANNEL_ID", None)
    monkeypatch.setattr(main, "PLAKY_API_KEY", None)
    monkeypatch.setattr(main, "call_plaky_bridge_invite", AsyncMock())
    monkeypatch.setattr(main, "resolve_member_email", AsyncMock(return_value=None))

    handled = await main._maybe_handle_plaky_add_request(message)

    assert handled is True
    assert "email" in "".join(str(c.args) for c in channel.send.call_args_list).lower()

    assert main.PENDING_PLAKY_EMAIL_THREADS.get(555, {}).get("discord_id") == 42


@pytest.mark.asyncio
async def test_pending_email_reply_completes_invite(monkeypatch):
    discord = __import__("discord")
    member = Mock(spec=discord.Member)
    member.id = 42
    member.display_name = "jane"
    member.global_name = "jane"
    member.name = "jane"
    member.bot = False

    thread = Mock(spec=discord.Thread)
    thread.id = 555
    thread.name = "ticket-555"
    thread.guild = SimpleNamespace()
    thread.owner_id = 42
    thread.parent_id = 100
    thread.send = AsyncMock()

    message = SimpleNamespace(
        id=998,
        guild=SimpleNamespace(),
        channel=thread,
        thread=None,
        content="jane@deepiri.com",
        author=member,
        mentions=[],
    )
    monkeypatch.setattr(main, "SUPPORT_SESSIONS_CHANNEL_ID", 100)
    monkeypatch.setattr(main, "GITHUB_PROFILES_CHANNEL_ID", None)
    monkeypatch.setattr(main, "PLAKY_API_KEY", "pk")
    bridge_invite = AsyncMock(return_value={"success": True, "via": "cake"})
    monkeypatch.setattr(main, "call_plaky_bridge_invite", bridge_invite)
    await main._pending_plaky_ask_set(555, {"discord_id": 42, "github_username": None, "role": "MEMBER", "requested_at": __import__("time").time(), "sender_id": 42})
    monkeypatch.setattr(main, "_resolve_reply_channel", AsyncMock(return_value=thread))

    handled = await main._maybe_handle_plaky_pending_email_reply(message)

    assert handled is True
    assert 555 not in main.PENDING_PLAKY_EMAIL_THREADS
    bridge_invite.assert_awaited_once_with("jane@deepiri.com", role="MEMBER")
    thread.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_request_mention_target_invites_with_cloud_email(monkeypatch):
    target = _member(discord_id=77, name="claire")
    sender = _member(discord_id=42, name="jane")
    channel = _ticket_channel(thread_id=601)
    message = _ticket_message(sender, channel, "add <@77> to the plaky please")
    message.mentions = [target]
    message.guild = SimpleNamespace(members=[target, sender])
    monkeypatch.setattr(main, "SUPPORT_SESSIONS_CHANNEL_ID", 100)
    monkeypatch.setattr(main, "GITHUB_PROFILES_CHANNEL_ID", None)
    monkeypatch.setattr(main, "PLAKY_API_KEY", "pk")
    bridge_invite = AsyncMock(return_value={"success": True, "via": "cake"})
    monkeypatch.setattr(main, "call_plaky_bridge_invite", bridge_invite)
    monkeypatch.setattr(main, "resolve_member_email", AsyncMock(return_value="claire@deepiri.com"))

    handled = await main._maybe_handle_plaky_add_request(message)

    assert handled is True
    bridge_invite.assert_awaited_once_with("claire@deepiri.com", role="MEMBER")
    assert "✅" in "".join(str(c.args) for c in channel.send.call_args_list).lower() or "✅" in "".join(str(c.args) for c in channel.send.call_args_list)


@pytest.mark.asyncio
async def test_ipca_sign_triggers_plaky_invite_and_asks_without_email(monkeypatch):
    member = _member(discord_id=42, name="jane")
    channel = _ticket_channel(thread_id=710)
    message = _ticket_message(member, channel, "I signed the IPCA")
    message.guild = SimpleNamespace(
        get_role=lambda rid: _member() if rid in (main.DEV_TEAM_ROLE_ID, main.AVAILABLE_ROLE_ID) else None
    )
    monkeypatch.setattr(main, "DEV_TEAM_ROLE_ID", 10)
    monkeypatch.setattr(main, "AVAILABLE_ROLE_ID", 20)
    monkeypatch.setattr(main, "SUPPORT_SESSIONS_CHANNEL_ID", 100)
    monkeypatch.setattr(main, "GITHUB_PROFILES_CHANNEL_ID", None)
    monkeypatch.setattr(main, "PLAKY_API_KEY", None)
    monkeypatch.setattr(main, "call_plaky_bridge_invite", AsyncMock())
    monkeypatch.setattr(main, "resolve_member_email", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "_resolve_reply_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(main, "_close_ticket_thread", AsyncMock())

    ipca_assigned = await main._maybe_auto_assign_ipca_roles(message)
    assert ipca_assigned is True

    # Same message also triggers the Plaky auto-invite; no email known -> in-thread ask.
    await main._maybe_auto_invite_to_plaky(message, member)

    assert main.PENDING_PLAKY_EMAIL_THREADS.get(710, {}).get("discord_id") == 42
    joined = " ".join(str(c.args) for c in channel.send.call_args_list)
    assert "email" in joined.lower()