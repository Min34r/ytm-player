"""Tests for the shared thread->loop coroutine dispatcher."""

import asyncio
import contextvars
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.services import _dispatch
from ytm_player.services._dispatch import capture_dispatch_context, dispatch_coro_threadsafe


async def test_holds_strong_task_ref_until_done():
    """The dispatcher must keep a strong reference to the scheduled task while
    it runs (so the GC can't reclaim it mid-flight) and release it once done."""
    baseline = len(_dispatch._pending_tasks)
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def cb() -> None:
        started.set()
        await proceed.wait()

    loop = asyncio.get_running_loop()
    assert dispatch_coro_threadsafe(loop, cb) is True

    await started.wait()  # task created and running
    assert len(_dispatch._pending_tasks) == baseline + 1  # strong ref held

    proceed.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if len(_dispatch._pending_tasks) == baseline:
            break
    assert len(_dispatch._pending_tasks) == baseline  # ref released after completion


async def test_scheduled_callback_actually_runs():
    """The dispatched coroutine runs on the loop."""
    ran = asyncio.Event()

    async def cb() -> None:
        ran.set()

    dispatch_coro_threadsafe(asyncio.get_running_loop(), cb)
    await asyncio.wait_for(ran.wait(), timeout=1)


def test_returns_false_when_loop_closed(caplog):
    """A closed loop (call_soon_threadsafe raises RuntimeError) yields False and
    a debug log, without raising."""
    loop = MagicMock()
    loop.call_soon_threadsafe.side_effect = RuntimeError("loop closed")

    with caplog.at_level(logging.DEBUG):
        result = dispatch_coro_threadsafe(loop, AsyncMock())

    assert result is False
    assert "Event loop closed" in caplog.text


_ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("dispatch_test_var", default="unset")


@pytest.fixture
def _reset_dispatch_context():
    _dispatch._dispatch_context = None
    yield
    _dispatch._dispatch_context = None


async def test_dispatch_applies_captured_context(_reset_dispatch_context):
    """The callback's task must run inside the context captured on the loop
    thread. Without it, call_soon_threadsafe uses the OS thread's empty
    context, Textual's active_app ContextVar is unset, and any timers or
    workers the callback arms (history reporting) die with LookupError."""
    _ctx_var.set("app-context")
    capture_dispatch_context()  # snapshot on the loop thread, like start()

    seen: list[str] = []
    done = asyncio.Event()

    async def cb() -> None:
        seen.append(_ctx_var.get())
        done.set()

    loop = asyncio.get_running_loop()
    # Dispatch from a real foreign thread, like a pynput/Quartz key event.
    await asyncio.to_thread(dispatch_coro_threadsafe, loop, cb)
    await asyncio.wait_for(done.wait(), timeout=1)

    assert seen == ["app-context"]


async def test_dispatch_without_capture_uses_default_context(_reset_dispatch_context):
    """No snapshot captured (services never started): dispatch still works,
    the callback just runs in the scheduling thread's default context."""
    assert _dispatch._dispatch_context is None
    done = asyncio.Event()
    seen: list[str] = []

    async def cb() -> None:
        seen.append(_ctx_var.get())
        done.set()

    loop = asyncio.get_running_loop()
    await asyncio.to_thread(dispatch_coro_threadsafe, loop, cb)
    await asyncio.wait_for(done.wait(), timeout=1)

    assert seen == ["unset"]
