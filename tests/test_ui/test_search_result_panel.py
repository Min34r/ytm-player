"""Tests for SearchResultPanel's right-click suppress flag.

A right-click on a panel ITEM fires both ItemRightClicked and a phantom
``ListView.Selected`` (Textual's ListItem forwards every mouse button), so
the panel arms ``_right_click_pending`` to swallow that one Selected. A
right-click on EMPTY panel space fires no Selected at all — arming the flag
there would silently eat the user's next legitimate selection instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ytm_player.ui.pages.search import SearchResultPanel


def _make_panel() -> tuple[SearchResultPanel, MagicMock]:
    panel = SearchResultPanel("Albums", id="albums-panel")
    panel._items = [{"title": "Album One", "browseId": "b1"}]
    posted = MagicMock(name="post_message")
    object.__setattr__(panel, "post_message", posted)
    return panel, posted


def _right_click_event(target) -> MagicMock:
    event = MagicMock(name="click")
    event.button = 3
    event.widget = target
    return event


class TestRightClickOnEmptySpace:
    def test_does_not_arm_the_suppress_flag(self):
        panel, posted = _make_panel()
        # The click target resolves to no ListItem (walk stops at the panel).
        panel.on_click(_right_click_event(target=panel))

        assert panel._right_click_pending is False
        posted.assert_not_called()

    def test_next_selection_still_goes_through(self):
        panel, posted = _make_panel()
        panel.on_click(_right_click_event(target=panel))

        select = MagicMock()
        select.list_view.index = 0
        panel.on_list_view_selected(select)

        assert posted.call_count == 1
        assert isinstance(posted.call_args.args[0], SearchResultPanel.ItemSelected)


class TestRightClickOnItem:
    def test_arms_flag_and_emits_right_clicked(self):
        panel, posted = _make_panel()
        with patch.object(panel, "_find_clicked_item_index", return_value=0):
            panel.on_click(_right_click_event(target=MagicMock()))

        assert panel._right_click_pending is True
        assert isinstance(posted.call_args.args[0], SearchResultPanel.ItemRightClicked)

    def test_swallows_exactly_one_phantom_selected(self):
        panel, posted = _make_panel()
        with patch.object(panel, "_find_clicked_item_index", return_value=0):
            panel.on_click(_right_click_event(target=MagicMock()))
        posted.reset_mock()

        select = MagicMock()
        select.list_view.index = 0
        panel.on_list_view_selected(select)  # the phantom — swallowed
        posted.assert_not_called()

        panel.on_list_view_selected(select)  # a real follow-up — delivered
        assert posted.call_count == 1
        assert isinstance(posted.call_args.args[0], SearchResultPanel.ItemSelected)
