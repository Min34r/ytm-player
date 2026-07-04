"""Tests for SearchPage.on_key: the search-mode toggle (default M-v) must
work while the search input has focus — the app-level dispatcher drops
keys when an Input is focused, so the page handles the binding itself."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from textual.events import Key

from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.config.keymap import KeyMap
from ytm_player.ui.pages.search import SearchPage


def _make_page(monkeypatch: pytest.MonkeyPatch, tmp_path, focused_id="search-input"):
    page = SearchPage()
    fake_app = MagicMock()
    fake_app._normalize_key = KeyHandlingMixin._normalize_key
    # Nonexistent path → default bindings only, hermetic from user config.
    fake_app.keymap = KeyMap.load(path=tmp_path / "no-keymap.toml")
    focused = MagicMock()
    focused.id = focused_id
    fake_app.focused = focused
    monkeypatch.setattr(type(page), "app", property(lambda self: fake_app))
    toggle = MagicMock()
    monkeypatch.setattr(page, "_toggle_search_mode", toggle)
    return page, toggle


def test_toggle_fires_while_input_focused(monkeypatch, tmp_path) -> None:
    page, toggle = _make_page(monkeypatch, tmp_path)
    event = Key("alt+v", None)

    page.on_key(event)

    toggle.assert_called_once()
    assert event._stop_propagation is True


def test_plain_typing_falls_through(monkeypatch, tmp_path) -> None:
    page, toggle = _make_page(monkeypatch, tmp_path)
    event = Key("a", "a")

    page.on_key(event)

    toggle.assert_not_called()
    assert event._stop_propagation is False


def test_toggle_left_to_app_dispatch_when_input_not_focused(monkeypatch, tmp_path) -> None:
    """With focus elsewhere the app-level dispatcher owns the binding —
    the page must not double-toggle."""
    page, toggle = _make_page(monkeypatch, tmp_path, focused_id="songs-table")
    event = Key("alt+v", None)

    page.on_key(event)

    toggle.assert_not_called()
