"""Tests for the Recently Played page's Local / YT Music tabs.

Covers the behaviour added when the page gained a second tab backed by
the YT Music account history (``get_history()``):

- the YT Music loader normalises + caps the server rows at ``_MAX_TRACKS``,
- empty / missing-service states render the right message,
- the local loader honours the same cap,
- keyboard tab switching (Enter on a focused tab label) works.

Like ``test_page_failure_states``, we exercise the page methods directly
and replace the widgets the page queries with ``MagicMock`` at the
``query_one`` boundary — no live Textual ``App``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.config.keymap import Action
from ytm_player.ui.pages.recently_played import (
    _MAX_TRACKS,
    _TAB_LOCAL,
    _TAB_YTM,
    RecentlyPlayedPage,
    RecentTab,
)


def _attach_fake_app(page, fake_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(page), "app", property(lambda self: fake_app))


def _make_page(active_tab: int = _TAB_LOCAL):
    """Build a page with every queried widget stubbed as a MagicMock."""
    page = RecentlyPlayedPage(active_tab=active_tab)

    widgets = {
        "#recent-loading": MagicMock(name="recent-loading"),
        "#recent-table": MagicMock(name="recent-table"),
        "#recent-footer": MagicMock(name="recent-footer"),
        "#recent-tab-local": MagicMock(name="recent-tab-local"),
        "#recent-tab-ytm": MagicMock(name="recent-tab-ytm"),
        "#track-filter": MagicMock(name="track-filter"),
    }
    widgets["#recent-table"].row_count = 0
    widgets["#recent-table"].cursor_row = None
    widgets["#recent-table"].selected_track = None
    widgets["#track-filter"].value = ""

    def fake_query_one(selector: str, _expected_type=None):
        return widgets[selector]

    object.__setattr__(page, "query_one", fake_query_one)
    return page, widgets


def _raw_tracks(n: int) -> list[dict]:
    """n playlistItem-shaped rows as returned by get_history()."""
    return [
        {
            "videoId": f"vid{i:04d}",
            "title": f"Song {i}",
            "artists": [{"name": "Artist", "id": "A1"}],
            "album": {"name": "Album", "id": "AL1"},
            "duration": "3:00",
            "played": "Today",
        }
        for i in range(n)
    ]


# ── YT Music loader ──────────────────────────────────────────────────


async def test_ytm_tab_caps_rows_at_max_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_history() returns ~200 unpaginated rows; the tab must slice to
    _MAX_TRACKS so the TUI stays responsive."""
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_history = AsyncMock(return_value=_raw_tracks(200))
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    fake_app._ytm_history = None  # not cached yet → fetch
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    widgets["#recent-table"].load_tracks.assert_called_once()
    loaded = widgets["#recent-table"].load_tracks.call_args.args[0]
    assert len(loaded) == _MAX_TRACKS
    # App-level cache holds the same capped list.
    assert len(fake_app._ytm_history) == _MAX_TRACKS


async def test_ytm_tab_empty_history_message(monkeypatch: pytest.MonkeyPatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_ytmusic = MagicMock()
    fake_ytmusic.get_history = AsyncMock(return_value=[])
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    fake_app._ytm_history = None
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    msgs = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("No YT Music play history found" in m for m in msgs), msgs


async def test_ytm_tab_error_shows_load_failed_message(monkeypatch: pytest.MonkeyPatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_ytmusic = MagicMock()
    # None signals a load failure (auth expired / network / server error).
    fake_ytmusic.get_history = AsyncMock(return_value=None)
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    fake_app._ytm_history = None
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert fake_app._ytm_history is None
    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    msgs = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("Couldn't load YT Music history" in m for m in msgs), msgs


async def test_ytm_tab_no_service_shows_auth_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """When there is no ytmusic service, the tab must prompt to sign in
    rather than claim the history is empty."""
    page, widgets = _make_page(active_tab=_TAB_YTM)

    fake_app = MagicMock()
    fake_app.ytmusic = None
    fake_app._ytm_history = None
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_ytm_history()

    assert page._ytm_auth_required is True
    widgets["#recent-table"].load_tracks.assert_called_once_with([])
    msgs = [c.args[0] for c in widgets["#recent-loading"].update.call_args_list]
    assert any("Sign in to YT Music" in m for m in msgs), msgs


# ── Local loader still honours the cap ───────────────────────────────


async def test_local_tab_requests_max_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)

    fake_history = MagicMock()
    fake_history.get_recently_played = AsyncMock(return_value=_raw_tracks(10))
    fake_app = MagicMock()
    fake_app.history = fake_history
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()

    fake_history.get_recently_played.assert_awaited_once_with(limit=_MAX_TRACKS)


# ── Optimistic add ───────────────────────────────────────────────────


async def test_optimistic_add_prepends_dedups_and_rerenders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A just-played track is prepended to the tab cache (deduped) and the
    Local tab re-renders live, matching the YT Music behaviour."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = MagicMock()
    _attach_fake_app(page, fake_app, monkeypatch)

    page._tab_cache[_TAB_LOCAL] = [
        {"video_id": "a"},
        {"video_id": "vid1"},
        {"video_id": "b"},
    ]

    page.optimistic_add(_TAB_LOCAL, {"video_id": "vid1", "title": "X"})

    ids = [t["video_id"] for t in page._tab_cache[_TAB_LOCAL]]
    assert ids == ["vid1", "a", "b"]
    widgets["#recent-table"].load_tracks.assert_called_once()


def test_optimistic_add_noop_without_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = MagicMock()
    _attach_fake_app(page, fake_app, monkeypatch)

    page.optimistic_add(_TAB_LOCAL, {"video_id": "vid1"})

    assert _TAB_LOCAL not in page._tab_cache
    widgets["#recent-table"].load_tracks.assert_not_called()


# ── Keyboard tab switching ───────────────────────────────────────────


async def test_enter_on_focused_tab_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a tab label focused, SELECT (Enter) switches to that tab."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)

    focused_tab = RecentTab("YT Music", _TAB_YTM, id="recent-tab-ytm")
    fake_app = MagicMock()
    fake_app.focused = focused_tab
    # Pre-seed the app-level YT Music cache so the switch takes the
    # no-refetch path.
    fake_app._ytm_history = _raw_tracks(3)
    _attach_fake_app(page, fake_app, monkeypatch)

    await page.handle_action(Action.SELECT)

    assert page._active_tab == _TAB_YTM
    widgets["#recent-table"].load_tracks.assert_called_once()


async def test_movement_on_focused_tab_drops_into_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """j/k while a tab is focused should move focus into the table, not
    switch tabs."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)

    focused_tab = RecentTab("YT Music", _TAB_YTM, id="recent-tab-ytm")
    fake_app = MagicMock()
    fake_app.focused = focused_tab
    _attach_fake_app(page, fake_app, monkeypatch)

    await page.handle_action(Action.MOVE_DOWN)

    assert page._active_tab == _TAB_LOCAL
    widgets["#recent-table"].focus.assert_called_once()


def test_reselecting_active_tab_reloads(monkeypatch) -> None:
    """Clicking / Enter on the already-active tab drops its cache and
    refetches, so the YT Music tab can be refreshed without leaving."""
    page, _ = _make_page(active_tab=_TAB_YTM)

    fake_app = MagicMock()
    fake_app._ytm_history = _raw_tracks(3)
    _attach_fake_app(page, fake_app, monkeypatch)
    monkeypatch.setattr(page, "_load_active_tab", MagicMock())

    page._switch_tab(_TAB_YTM)  # same tab → refresh

    assert fake_app._ytm_history is None
    page._load_active_tab.assert_called_once()
    fake_app.notify.assert_called_once()


# ── background refresh (focus / filter / cursor) ─────────────────────


def _page_with_cache(monkeypatch, tracks):
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = MagicMock()
    _attach_fake_app(page, fake_app, monkeypatch)
    page._set_cache(_TAB_LOCAL, tracks)
    return page, widgets


def test_background_refresh_does_not_steal_focus(monkeypatch) -> None:
    page, widgets = _page_with_cache(monkeypatch, [{"video_id": "a", "title": "A"}])
    widgets["#recent-table"].tracks = []

    page._refresh_tab_from_cache(_TAB_LOCAL)

    widgets["#recent-table"].focus.assert_not_called()


def test_initial_display_still_focuses_table(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = MagicMock()
    _attach_fake_app(page, fake_app, monkeypatch)
    widgets["#recent-table"].row_count = 1

    page._display_tracks([{"video_id": "a", "title": "A"}])

    widgets["#recent-table"].focus.assert_called_once()


def test_background_refresh_reapplies_active_filter(monkeypatch) -> None:
    page, widgets = _page_with_cache(
        monkeypatch,
        [{"video_id": "a", "title": "Alpha"}, {"video_id": "b", "title": "Beta"}],
    )
    widgets["#track-filter"].value = "alp"
    widgets["#recent-table"].tracks = []

    page._refresh_tab_from_cache(_TAB_LOCAL)

    widgets["#recent-table"].load_tracks.assert_called_once()
    widgets["#recent-table"].apply_filter.assert_called_once_with("alp")


async def test_local_failure_does_not_leak_onto_ytm_tab(monkeypatch) -> None:
    """A failed Local load then a YTM visit with 0 rows must show the YTM
    empty message, not the local-failure copy."""
    page, widgets = _make_page(active_tab=_TAB_LOCAL)
    fake_app = MagicMock()
    fake_app.history.get_recently_played = AsyncMock(side_effect=OSError("locked"))
    fake_ytmusic = MagicMock()
    fake_ytmusic.get_history = AsyncMock(return_value=[])
    fake_app.ytmusic = fake_ytmusic
    fake_app._ytm_history = None
    _attach_fake_app(page, fake_app, monkeypatch)

    await page._load_history()
    assert page._load_failed is True

    page._active_tab = _TAB_YTM
    await page._load_ytm_history()

    msg = str(widgets["#recent-loading"].update.call_args.args[0])
    assert "No YT Music play history found." in msg
    # The local flag survives for the local tab's own retry logic.
    assert page._load_failed is True


async def test_ytm_visit_does_not_clear_local_failure_flag(monkeypatch) -> None:
    page, widgets = _make_page(active_tab=_TAB_YTM)
    fake_ytmusic = MagicMock()
    fake_ytmusic.get_history = AsyncMock(return_value=_raw_tracks(3))
    fake_app = MagicMock()
    fake_app.ytmusic = fake_ytmusic
    fake_app._ytm_history = None
    _attach_fake_app(page, fake_app, monkeypatch)
    page._load_failed = True  # local tab failed earlier

    await page._load_ytm_history()

    assert page._load_failed is True


def test_background_refresh_keeps_cursor_on_same_track(monkeypatch) -> None:
    """A dedup-move refresh is net-zero: the cursored track keeps its row.

    Identity comes from ``selected_track`` (the highlighted VISIBLE row,
    mapped through any active sort) — a backing-list index would restore
    to the wrong track in a sorted view.
    """
    new = [{"video_id": "b", "title": "B"}, {"video_id": "a", "title": "A"}]
    page, widgets = _page_with_cache(monkeypatch, new)
    widgets["#recent-table"].selected_track = {"video_id": "a", "title": "A"}
    widgets["#recent-table"].row_count = 2

    page._refresh_tab_from_cache(_TAB_LOCAL)

    # "a" is at index 1 in the new cache; move_cursor lands there.
    widgets["#recent-table"].move_cursor.assert_called_once_with(row=1)
