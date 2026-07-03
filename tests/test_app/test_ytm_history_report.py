"""Tests for reporting TUI plays back to the YT Music account history.

Reporting is scheduled with a wall-clock timer so it still fires when the
terminal loses focus but mpv keeps playing. A generation check prevents quick
skips from being logged; a tiny ``position > 0`` guard verifies playback
actually started. Opt-out (default on).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._playback import (
    _YTM_HISTORY_MAX,
    _YTM_HISTORY_POLL_SECONDS,
    PlaybackMixin,
    _LocalHistoryClaim,
)
from ytm_player.config.settings import DEFAULT_HISTORY_MIN_LISTEN_SECONDS


def _host(
    *,
    enabled: bool = True,
    ytmusic: object | None = "default",
    play_generation: int = 1,
    reported_generation: int = -1,
    position: float = 1.0,
    video_id: str = "vid1",
) -> MagicMock:
    host = MagicMock()
    host.settings.playback.sync_history_to_ytmusic = enabled
    host.settings.playback.history_min_listen_seconds = DEFAULT_HISTORY_MIN_LISTEN_SECONDS
    host.ytmusic = MagicMock() if ytmusic == "default" else ytmusic
    host._play_generation = play_generation
    host._ytm_reported_generation = reported_generation
    host._ytm_history = None
    host._local_history_claim = None
    host._track_start_position = 0.0
    host.history = MagicMock()
    host.player.position = position
    host.player.current_track = {"video_id": video_id} if video_id else None
    host.set_timer = MagicMock()
    host.run_worker = MagicMock()
    host._history_min_listen_seconds = PlaybackMixin._history_min_listen_seconds.__get__(host)
    host._history_timer_delay = PlaybackMixin._history_timer_delay.__get__(host)
    # Bind the real poll callbacks so re-armed timer lambdas (which call
    # self._report_*) exercise the real chain instead of a MagicMock no-op.
    host._report_local_play = PlaybackMixin._report_local_play.__get__(host)
    host._report_ytm_play = PlaybackMixin._report_ytm_play.__get__(host)
    # Bind the push coroutine too: _report_ytm_play hands it to run_worker,
    # and an auto-mocked method there would hide the real coroutine contract.
    host._push_ytm_history_report = PlaybackMixin._push_ytm_history_report.__get__(host)
    return host


def _schedule(host, video_id="vid1", generation=1) -> None:
    track = {"video_id": video_id}
    PlaybackMixin._schedule_ytm_history_report.__get__(host)(track, video_id, generation)


def _schedule_local(host, video_id="vid1", generation=1) -> None:
    track = {"video_id": video_id}
    PlaybackMixin._schedule_local_history_log.__get__(host)(track, video_id, generation)


def _report(host, video_id="vid1", generation=1) -> None:
    track = {"video_id": video_id}
    PlaybackMixin._report_ytm_play.__get__(host)(track, video_id, generation)


def _claim(video_id: str = "vid1", generation: int = 1, **kw) -> _LocalHistoryClaim:
    c = _LocalHistoryClaim(video_id=video_id, track={"video_id": video_id}, generation=generation)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _report_local(host, claim) -> None:
    PlaybackMixin._report_local_play.__get__(host)(claim)


def _finalize(host, video_id="vid1"):
    return PlaybackMixin._log_local_listen.__get__(host)({"video_id": video_id})


def _insert(host, claim, listened=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1):
    return PlaybackMixin._insert_local_history_play.__get__(host)(claim, listened)


# ── scheduling ───────────────────────────────────────────────────────


def test_schedule_arms_initial_threshold_timer() -> None:
    host = _host()
    _schedule(host)
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == DEFAULT_HISTORY_MIN_LISTEN_SECONDS


def test_schedule_uses_configured_history_threshold() -> None:
    host = _host()
    host.settings.playback.history_min_listen_seconds = 30
    _schedule(host)
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == 30


def test_schedule_never_arms_timer_with_zero_delay() -> None:
    """threshold=0 ("count any playback") is valid gating, but set_timer(0)
    raises ZeroDivisionError inside Textual and kills the report timer."""
    host = _host()
    host.settings.playback.history_min_listen_seconds = 0
    _schedule(host)
    _schedule_local(host)
    for call in host.set_timer.call_args_list:
        assert call.args[0] > 0


def test_local_schedule_never_arms_timer_with_zero_delay() -> None:
    host = _host()
    host.settings.playback.history_min_listen_seconds = 0
    _schedule_local(host)
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] > 0


def test_schedule_skips_when_disabled() -> None:
    host = _host(enabled=False)
    _schedule(host)
    host.set_timer.assert_not_called()


def test_schedule_skips_without_ytmusic() -> None:
    host = _host(ytmusic=None)
    _schedule(host)
    host.set_timer.assert_not_called()


def test_schedule_skips_without_video_id() -> None:
    host = _host()
    _schedule(host, video_id="")
    host.set_timer.assert_not_called()


# ── timer callback ───────────────────────────────────────────────────


def test_report_schedules_push_worker_once_threshold_crossed() -> None:
    host = _host(play_generation=3, position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    _report(host, generation=3)
    host.run_worker.assert_called_once()
    # Marking is deferred to the worker so failures are never shown as done.
    assert host._ytm_reported_generation == -1
    host.run_worker.call_args.args[0].close()  # unawaited coroutine cleanup


def test_report_skips_stale_generation() -> None:
    """A track skipped before the timer fired bumped the generation."""
    host = _host(play_generation=5)
    _report(host, generation=4)
    host.run_worker.assert_not_called()


def test_report_repolls_while_position_below_threshold() -> None:
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS - 1)
    _report(host)
    host.ytmusic.add_history_item.assert_not_called()
    host.run_worker.assert_not_called()
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == _YTM_HISTORY_POLL_SECONDS


def test_report_uses_configured_history_threshold() -> None:
    host = _host(position=10)
    host.settings.playback.history_min_listen_seconds = 30
    _report(host)
    host.ytmusic.add_history_item.assert_not_called()
    host.run_worker.assert_not_called()
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == _YTM_HISTORY_POLL_SECONDS


def test_zero_threshold_counts_positive_playback_time() -> None:
    host = _host(position=0)
    host.settings.playback.history_min_listen_seconds = 0
    _report(host)
    host.ytmusic.add_history_item.assert_not_called()
    host.run_worker.assert_not_called()

    host = _host(position=1)
    host.settings.playback.history_min_listen_seconds = 0
    _report(host)
    host.run_worker.assert_called_once()
    host.run_worker.call_args.args[0].close()  # unawaited coroutine cleanup


def test_report_repolls_when_resumed_below_relative_threshold() -> None:
    """On resume-on-launch the track starts mid-file, so raw position is high
    but the actual listen time (position - start) is still below threshold."""
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 100)
    host._track_start_position = float(DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 100 - 1)  # heard ~1s
    _report(host)
    host.ytmusic.add_history_item.assert_not_called()
    host.run_worker.assert_not_called()
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == _YTM_HISTORY_POLL_SECONDS


def test_report_fires_when_resumed_and_listened_past_threshold() -> None:
    host = _host(position=200.0, play_generation=3)
    host._track_start_position = float(200 - DEFAULT_HISTORY_MIN_LISTEN_SECONDS - 1)  # heard 6s
    _report(host, generation=3)
    host.run_worker.assert_called_once()
    host.run_worker.call_args.args[0].close()  # unawaited coroutine cleanup


def test_report_skips_when_already_reported() -> None:
    host = _host(play_generation=2, reported_generation=2)
    _report(host, generation=2)
    host.run_worker.assert_not_called()


def test_report_skips_when_disabled_mid_play() -> None:
    host = _host(enabled=False)
    _report(host)
    host.run_worker.assert_not_called()


# ── local SQLite history ─────────────────────────────────────────────


def test_local_schedule_arms_threshold_timer_and_creates_claim() -> None:
    host = _host()
    _schedule_local(host)
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == DEFAULT_HISTORY_MIN_LISTEN_SECONDS
    claim = host._local_history_claim
    assert claim is not None and claim.video_id == "vid1" and not claim.finalized


def test_local_schedule_replaces_previous_claim() -> None:
    """Each committed play gets its OWN claim — same-video replays included."""
    host = _host()
    _schedule_local(host)
    first = host._local_history_claim
    _schedule_local(host)  # same video_id, new play
    assert host._local_history_claim is not first


def test_local_report_repolls_until_position_exceeds_threshold() -> None:
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS)
    claim = _claim()
    host._local_history_claim = claim
    _report_local(host, claim)
    host.run_worker.assert_not_called()
    host.set_timer.assert_called_once()
    assert host.set_timer.call_args.args[0] == _YTM_HISTORY_POLL_SECONDS


def test_local_report_inserts_once_position_exceeds_threshold() -> None:
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    claim = _claim()
    host._local_history_claim = claim
    _report_local(host, claim)
    host.run_worker.assert_called_once()
    assert claim.insert_started is True


def test_local_report_stops_when_claim_superseded() -> None:
    """A newer play replaced the claim: the old chain must die, not re-arm."""
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    old = _claim()
    host._local_history_claim = _claim()  # a different object
    _report_local(host, old)
    host.run_worker.assert_not_called()
    host.set_timer.assert_not_called()


def test_local_report_stops_after_finalize() -> None:
    """Bug (g): finalize direct-logged the row; the still-armed timer must
    not claim + insert a second row for the same play."""
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    claim = _claim(finalized=True)
    host._local_history_claim = claim
    _report_local(host, claim)
    host.run_worker.assert_not_called()
    host.set_timer.assert_not_called()


def test_local_report_fires_even_when_current_track_cleared() -> None:
    """Natural advance: a duplicate mpv end-file clears player.current_track
    while the track keeps playing. The report must still fire."""
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    host.player.current_track = None
    claim = _claim()
    host._local_history_claim = claim
    _report_local(host, claim)
    host.run_worker.assert_called_once()


def test_ytm_report_fires_even_when_current_track_cleared() -> None:
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    host.player.current_track = None
    _report(host)
    host.run_worker.assert_called_once()
    host.run_worker.call_args.args[0].close()  # unawaited coroutine cleanup


async def test_push_marks_and_prepends_only_on_server_accept() -> None:
    host = _host(play_generation=3, position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    host.ytmusic.add_history_item = AsyncMock(return_value=True)
    host._ytm_history = [{"video_id": "old"}]
    host._get_current_page.return_value = MagicMock()  # not RecentlyPlayedPage
    host._optimistic_ytm_history_add = PlaybackMixin._optimistic_ytm_history_add.__get__(host)

    await PlaybackMixin._push_ytm_history_report.__get__(host)(
        {"video_id": "vid1", "title": "X"}, "vid1", 3
    )

    host.ytmusic.add_history_item.assert_awaited_once_with("vid1")
    assert host._ytm_reported_generation == 3
    assert host._ytm_history[0]["video_id"] == "vid1"


async def test_push_failure_leaves_tab_and_marking_untouched() -> None:
    """Expired session: the tab must not show phantom rows all session."""
    host = _host(play_generation=3, position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    host.ytmusic.add_history_item = AsyncMock(return_value=False)
    host._ytm_history = [{"video_id": "old"}]

    await PlaybackMixin._push_ytm_history_report.__get__(host)(
        {"video_id": "vid1", "title": "X"}, "vid1", 3
    )

    assert host._ytm_reported_generation == -1
    assert host._ytm_history == [{"video_id": "old"}]


def test_local_poll_terminates_on_stable_idle() -> None:
    """Stream error / stop / end-of-queue: two consecutive idle polls end
    the chain instead of re-arming 1 Hz timers forever."""
    host = _host(position=0.0)
    host.player.current_track = None
    claim = _claim()
    host._local_history_claim = claim
    _report_local(host, claim)  # idle poll #1 → re-arms once more
    assert host.set_timer.call_count == 1
    host.set_timer.call_args.args[1]()  # fires poll #2, still idle → STOP
    assert host.set_timer.call_count == 1
    host.run_worker.assert_not_called()


def test_local_poll_survives_transient_current_track_clear() -> None:
    """One idle poll (the documented duplicate-end-file transient) must NOT
    kill the chain; the counter resets when the track is back."""
    host = _host(position=1.0)
    claim = _claim()
    host._local_history_claim = claim
    host.player.current_track = None  # transient clear
    _report_local(host, claim)  # idle #1 → re-arm
    assert host.set_timer.call_count == 1
    host.player.current_track = {"video_id": "vid1"}  # track is back
    host.set_timer.call_args.args[1]()  # poll: resets idle
    assert host.set_timer.call_count == 2
    host.player.current_track = None  # idle again (fresh)
    host.set_timer.call_args.args[1]()  # idle #1 again → re-arm
    assert host.set_timer.call_count == 3


def test_ytm_poll_terminates_on_stable_idle() -> None:
    host = _host(position=0.0)
    host.player.current_track = None
    _report(host)  # idle poll #1 → re-arms once more
    assert host.set_timer.call_count == 1
    host.set_timer.call_args.args[1]()  # poll #2, still idle → STOP
    assert host.set_timer.call_count == 1
    host.run_worker.assert_not_called()


async def test_local_insert_records_row_id_on_claim() -> None:
    host = _host(play_generation=4)
    claim = _claim(generation=4, insert_started=True)
    host._local_history_claim = claim
    host.history.log_play = AsyncMock(return_value=123)
    host._optimistic_local_history_add = MagicMock()

    await _insert(host, claim)

    assert claim.row_id == 123
    host._optimistic_local_history_add.assert_called_once()


async def test_local_insert_still_logs_when_superseded_after_threshold() -> None:
    """A skip right after crossing the threshold must not drop the row —
    and must not optimistically render a stale play."""
    host = _host(play_generation=5)
    claim = _claim(generation=4, insert_started=True)
    host._local_history_claim = claim
    host.history.log_play = AsyncMock(return_value=123)
    host._optimistic_local_history_add = MagicMock()

    await _insert(host, claim)

    host.history.log_play.assert_awaited_once()
    assert claim.row_id == 123
    host._optimistic_local_history_add.assert_not_called()


async def test_stale_insert_still_logs_earned_row_and_preserves_new_claim() -> None:
    """INVERTED from the old suite: a late worker for track A logs A's
    earned row anyway, and cannot touch track B's claim (it only writes its
    own object)."""
    host = _host(play_generation=2)
    claim_a = _claim("vidA", insert_started=True)  # stale: generation 1
    claim_b = _claim("vidB", generation=2, insert_started=True, pending_seconds=42)
    host._local_history_claim = claim_b
    host.history.log_play = AsyncMock(return_value=999)
    host._optimistic_local_history_add = MagicMock()

    await _insert(host, claim_a)

    host.history.log_play.assert_awaited_once()
    assert claim_a.row_id == 999
    assert host._local_history_claim is claim_b
    assert claim_b.pending_seconds == 42
    host._optimistic_local_history_add.assert_not_called()


async def test_local_insert_survives_mid_write_reclaim() -> None:
    """Bug (d)/(e) regression: track B claims while A's log_play is awaited;
    A's worker must not wipe or consume B's state."""
    host = _host()
    claim_a = _claim("vidA", insert_started=True)
    host._local_history_claim = claim_a
    claim_b = _claim("vidB", generation=2, insert_started=True, pending_seconds=99)

    async def _log_play(*_a, **_k):
        host._local_history_claim = claim_b  # B claims mid-write
        host._play_generation = 2
        return 123

    host.history.log_play = AsyncMock(side_effect=_log_play)
    host.history.update_play_listened_seconds = AsyncMock()

    await _insert(host, claim_a)

    assert claim_a.row_id == 123
    assert host._local_history_claim is claim_b
    assert claim_b.pending_seconds == 99
    host.history.update_play_listened_seconds.assert_not_awaited()


async def test_local_insert_applies_pending_duration() -> None:
    """Finalize ran while the insert was in flight; the final duration wins."""
    host = _host()
    claim = _claim(insert_started=True, finalized=True, pending_seconds=90)
    host._local_history_claim = claim
    host.history.log_play = AsyncMock(return_value=123)
    host.history.update_play_listened_seconds = AsyncMock()

    await _insert(host, claim)

    host.history.update_play_listened_seconds.assert_awaited_once_with(123, 90)
    assert claim.pending_seconds is None


async def test_superseded_insert_still_applies_pending_duration() -> None:
    """Bug (f) regression: a NEW play claiming must not cost the in-flight
    play its final duration — pending lives on the old play's own claim and
    the worker applies it even though the claim was replaced."""
    host = _host()
    claim_a = _claim("vidA", insert_started=True, finalized=True, pending_seconds=90)
    host._local_history_claim = _claim("vidB")  # B already claimed
    host.history.log_play = AsyncMock(return_value=123)
    host.history.update_play_listened_seconds = AsyncMock()

    await _insert(host, claim_a)

    host.history.update_play_listened_seconds.assert_awaited_once_with(123, 90)
    assert claim_a.pending_seconds is None


async def test_local_insert_failure_leaves_row_unset() -> None:
    host = _host()
    claim = _claim(insert_started=True)
    host._local_history_claim = claim
    host.history.log_play = AsyncMock(side_effect=OSError("db gone"))
    host._optimistic_local_history_add = MagicMock()

    await _insert(host, claim)

    assert claim.row_id is None
    host._optimistic_local_history_add.assert_not_called()


async def test_final_local_log_updates_existing_row() -> None:
    host = _host(position=60)
    claim = _claim(row_id=123, insert_started=True)
    host._local_history_claim = claim
    host.history.update_play_listened_seconds = AsyncMock()

    await _finalize(host)

    host.history.update_play_listened_seconds.assert_awaited_once_with(123, 60)
    assert claim.finalized is True


async def test_final_local_log_hands_off_when_insert_in_flight() -> None:
    """Finalize while the worker is mid-insert stashes the duration on the
    claim for the worker to apply."""
    host = _host(position=90)
    claim = _claim(insert_started=True)
    host._local_history_claim = claim
    host.history.update_play_listened_seconds = AsyncMock()
    host.history.log_play = AsyncMock()

    await _finalize(host)

    host.history.update_play_listened_seconds.assert_not_awaited()
    host.history.log_play.assert_not_awaited()
    assert claim.pending_seconds == 90
    assert claim.finalized is True


async def test_finalize_consumes_claim_even_when_listened_zero() -> None:
    """Bug (a) regression: mpv reads position 0.0 once idle, so natural-EOF
    finalize computes listened <= 0. The claim must STILL be consumed or a
    same-video replay (repeat-one) merges into the old row."""
    host = _host(position=0.0)
    claim = _claim(row_id=123, insert_started=True)
    host._local_history_claim = claim
    host.history.update_play_listened_seconds = AsyncMock()

    await _finalize(host)

    host.history.update_play_listened_seconds.assert_not_awaited()
    assert claim.finalized is True


def test_same_video_replay_gets_fresh_claim_and_new_insert() -> None:
    """Bug (a)+(b): after a zero-listen finalize consumed the old claim,
    replaying the same video schedules a fresh claim whose chain inserts
    normally — no video_id-keyed bail, no lost replay. (The stale-finalize
    race against a fresh same-video claim is closed in _on_track_end and
    covered by TestTrackEndFinalize in test_playback.py.)"""
    host = _host(position=DEFAULT_HISTORY_MIN_LISTEN_SECONDS + 1)
    host._local_history_claim = _claim(finalized=True)  # consumed old play
    _schedule_local(host)  # replay of the same video
    new_claim = host._local_history_claim
    assert new_claim is not None and not new_claim.finalized
    _report_local(host, new_claim)
    host.run_worker.assert_called_once()


async def test_second_finalize_is_noop() -> None:
    """Duplicate end-file / concurrent supersede: the second finalize for the
    same play must not write a second row."""
    host = _host(position=60)
    claim = _claim(row_id=123, insert_started=True, finalized=True)
    host._local_history_claim = claim
    host.history.update_play_listened_seconds = AsyncMock()
    host.history.log_play = AsyncMock()

    await _finalize(host)

    host.history.update_play_listened_seconds.assert_not_awaited()
    host.history.log_play.assert_not_awaited()


async def test_finalize_direct_logs_when_claim_belongs_to_other_video() -> None:
    """Late finalize for a track that never claimed: direct-log it (log_play
    enforces the threshold) and leave the current claim untouched."""
    host = _host(position=60)
    claim_b = _claim("vidB", insert_started=True)
    host._local_history_claim = claim_b
    host.history.log_play = AsyncMock(return_value=7)
    host.history.update_play_listened_seconds = AsyncMock()

    await _finalize(host, video_id="vidA")

    host.history.log_play.assert_awaited_once()
    assert host.history.log_play.call_args.kwargs["track"] == {"video_id": "vidA"}
    host.history.update_play_listened_seconds.assert_not_awaited()
    assert claim_b.finalized is False


async def test_finalize_direct_logs_when_no_claim_exists() -> None:
    """No claim at all (below-threshold play that ended between polls):
    finalize direct-logs; log_play's own threshold decides if it counts."""
    host = _host(position=60)
    host._local_history_claim = None
    host.history.log_play = AsyncMock(return_value=7)

    await _finalize(host)

    host.history.log_play.assert_awaited_once()


# ── optimistic cache update ──────────────────────────────────────────


def _add(host, video_id="vid1", track=None) -> None:
    if track is None:
        track = {"video_id": video_id, "title": "X"}
    PlaybackMixin._optimistic_ytm_history_add.__get__(host)(track, video_id)


def test_optimistic_noop_when_cache_unfetched() -> None:
    host = MagicMock()
    host._ytm_history = None
    _add(host)
    assert host._ytm_history is None


def test_optimistic_prepends_and_dedups() -> None:
    host = MagicMock()
    host._ytm_history = [{"video_id": "a"}, {"video_id": "vid1"}, {"video_id": "b"}]
    host._get_current_page.return_value = MagicMock()  # not a RecentlyPlayedPage
    _add(host, "vid1", {"video_id": "vid1", "title": "X"})
    ids = [t["video_id"] for t in host._ytm_history]
    assert ids == ["vid1", "a", "b"]


def test_optimistic_uses_passed_track_not_current_track() -> None:
    """On natural advance a duplicate end-file can clear player.current_track;
    the optimistic add must still use the track captured at schedule time."""
    host = MagicMock()
    host._ytm_history = [{"video_id": "a"}]
    host.player.current_track = None  # cleared by duplicate end-file
    host._get_current_page.return_value = MagicMock()
    _add(host, "vid1", {"video_id": "vid1", "title": "X"})
    assert [t["video_id"] for t in host._ytm_history] == ["vid1", "a"]


def test_optimistic_caps_length() -> None:
    host = MagicMock()
    host._ytm_history = [{"video_id": f"v{i}"} for i in range(_YTM_HISTORY_MAX)]
    host._get_current_page.return_value = MagicMock()
    _add(host, "new", {"video_id": "new", "title": "X"})
    assert len(host._ytm_history) == _YTM_HISTORY_MAX
    assert host._ytm_history[0]["video_id"] == "new"


def test_optimistic_entry_matches_server_row_schema() -> None:
    """Optimistically-added rows and server rows (normalize_tracks output) both
    feed the same cache/TrackTable, so they must share the display schema.
    Tracks flowing through playback are already normalized; this locks that
    contract so the two data paths can't silently diverge.
    """
    from ytm_player.utils.formatting import normalize_tracks

    raw = {
        "videoId": "vid1",
        "title": "Song",
        "artists": [{"name": "Artist"}],
        "album": {"name": "Album"},
        "duration": "3:00",
    }
    server_row = normalize_tracks([raw])[0]

    host = MagicMock()
    host._ytm_history = []
    host._get_current_page.return_value = MagicMock()
    # A track flowing through playback is a normalized dict (same as server_row).
    _add(host, "vid1", dict(server_row))
    entry = host._ytm_history[0]

    # The keys TrackTable renders from must be present on optimistic entries.
    display_keys = {"video_id", "title", "artist", "artists", "album", "duration"}
    assert display_keys <= entry.keys()
    assert display_keys <= server_row.keys()


# ── optimistic local update ──────────────────────────────────────────


def test_optimistic_local_add_calls_page_when_open() -> None:
    from ytm_player.ui.pages.recently_played import _TAB_LOCAL, RecentlyPlayedPage

    host = MagicMock()
    page = MagicMock(spec=RecentlyPlayedPage)
    host._get_current_page.return_value = page
    track = {"video_id": "vid1", "title": "X"}

    PlaybackMixin._optimistic_local_history_add.__get__(host)(track)

    page.optimistic_add.assert_called_once_with(_TAB_LOCAL, track)


def test_optimistic_local_add_noop_when_page_closed() -> None:
    host = MagicMock()
    host._get_current_page.return_value = MagicMock()  # not a RecentlyPlayedPage
    PlaybackMixin._optimistic_local_history_add.__get__(host)({"video_id": "vid1"})
    # No RecentlyPlayedPage → nothing to assert beyond no exception.
