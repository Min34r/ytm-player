"""Tests for DiscordRPC (no external dependencies needed)."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from ytm_player.services.discord_rpc import DEFAULT_DISCORD_CLIENT_ID, DiscordRPC


def _install_fake_pypresence(monkeypatch):
    class FakePresence:
        instances: list[FakePresence] = []
        update_failures = 0
        clear_failures = 0

        def __init__(self, client_id: str) -> None:
            self.client_id = client_id
            self.connected = False
            self.closed = False
            self.updates: list[dict] = []
            self.clears = 0
            FakePresence.instances.append(self)

        async def connect(self) -> None:
            self.connected = True

        def close(self) -> None:
            self.closed = True

        async def update(self, **kwargs) -> None:
            self.updates.append(kwargs)
            if FakePresence.update_failures > 0:
                FakePresence.update_failures -= 1
                raise RuntimeError("pipe closed")

        async def clear(self) -> None:
            self.clears += 1
            if FakePresence.clear_failures > 0:
                FakePresence.clear_failures -= 1
                raise RuntimeError("pipe closed")

    fake = ModuleType("pypresence")
    fake.AioPresence = FakePresence
    fake.ActivityType = SimpleNamespace(LISTENING="listening")
    monkeypatch.setitem(sys.modules, "pypresence", fake)
    return FakePresence


class TestDiscordRPCInit:
    def test_initial_state(self):
        rpc = DiscordRPC()
        assert rpc.is_connected is False
        assert rpc._rpc is None
        assert rpc._start_time == 0

    def test_default_client_id(self):
        """No client_id given → uses the bundled default."""
        assert DiscordRPC()._client_id == DEFAULT_DISCORD_CLIENT_ID

    def test_custom_client_id(self):
        """A user-supplied client_id is honoured (and trimmed)."""
        assert DiscordRPC(client_id="998877665544332211")._client_id == "998877665544332211"
        assert DiscordRPC(client_id="  998877665544332211  ")._client_id == "998877665544332211"

    def test_empty_client_id_falls_back(self):
        """Empty or whitespace-only client_id falls back to the default (#88)."""
        assert DiscordRPC(client_id="")._client_id == DEFAULT_DISCORD_CLIENT_ID
        assert DiscordRPC(client_id="   ")._client_id == DEFAULT_DISCORD_CLIENT_ID


class TestDiscordRPCReconnect:
    async def test_update_reconnects_and_retries_once_after_rpc_failure(self, monkeypatch):
        fake_presence = _install_fake_pypresence(monkeypatch)
        rpc = DiscordRPC(client_id="123")

        assert await rpc.connect() is True
        fake_presence.update_failures = 1

        await rpc.update(title="Song", artist="Artist", album="Album", duration=180)

        assert rpc.is_connected is True
        assert len(fake_presence.instances) == 2
        assert fake_presence.instances[0].closed is True
        assert fake_presence.instances[1].updates[0]["details"] == "Song"
        assert fake_presence.instances[1].updates[0]["state"] == "Artist"

    async def test_clear_reconnects_and_retries_once_after_rpc_failure(self, monkeypatch):
        fake_presence = _install_fake_pypresence(monkeypatch)
        rpc = DiscordRPC(client_id="123")

        assert await rpc.connect() is True
        fake_presence.clear_failures = 1

        await rpc.clear()

        assert rpc.is_connected is True
        assert len(fake_presence.instances) == 2
        assert fake_presence.instances[0].closed is True
        assert fake_presence.instances[1].clears == 1
