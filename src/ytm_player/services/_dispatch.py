"""Bridge coroutine callbacks from foreign OS threads onto the asyncio loop.

The platform media-key services (Windows pynput, macOS event-tap and Now
Playing) receive key events on their own OS threads and must run the app's
async player callbacks on the event loop.  Scheduling ``asyncio.ensure_future``
without keeping a reference lets the garbage collector reclaim the pending task
mid-flight — the same footgun ``services/player.py`` guards against by holding
task refs.  This module centralises the safe idiom and the shared callback type.

``asyncio`` is imported lazily inside the dispatch function on purpose: importing
it at module top would pull asyncio's win32 branch (which needs the ``_overlapped``
extension) when ``services/mpris.py`` — which imports ``PlayerCallback`` from here
unconditionally — is imported under a faked win32 ``sys.platform`` by the MPRIS
platform-guard test.  The type alias needs only ``collections.abc``.
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio

logger = logging.getLogger(__name__)

# Async callback signature shared by every platform media service.
PlayerCallback = Callable[..., Coroutine[Any, Any, None]]

# Strong references to in-flight dispatched tasks so the GC can't collect them
# before they finish (asyncio only holds a weak reference to a bare task).
_pending_tasks: set[asyncio.Task[Any]] = set()

# Context snapshot from the app's loop thread. ``call_soon_threadsafe`` runs
# its callback in the CALLING thread's (empty) context, so tasks spawned for
# media-key events would lose Textual's ``active_app`` ContextVar — any
# timers/workers the callback arms (history reporting, etc.) then die with
# LookupError. The platform services capture this when they take their loop
# reference (on the loop thread); every dispatch is applied within it. Same
# fix as Player.set_event_loop's ``_dispatch_context``.
_dispatch_context: contextvars.Context | None = None


def capture_dispatch_context() -> None:
    """Snapshot the current contextvars context for future dispatches.

    Must run on the app's event-loop thread — the platform services'
    ``start()`` methods are called there — so the snapshot carries
    Textual's ``active_app`` ContextVar.
    """
    global _dispatch_context
    try:
        _dispatch_context = contextvars.copy_context()
    except Exception:
        _dispatch_context = None


def dispatch_coro_threadsafe(
    loop: asyncio.AbstractEventLoop,
    callback: PlayerCallback,
) -> bool:
    """Schedule ``callback()`` on *loop* from a non-loop (OS) thread.

    Creates the task on the loop and holds a strong reference to it until it
    completes.  Returns ``True`` if the coroutine was scheduled, ``False`` if
    the loop had already closed.  Safe to call from any thread.
    """
    import asyncio

    def _spawn() -> None:
        task = asyncio.ensure_future(callback())
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)

    try:
        if _dispatch_context is not None:
            loop.call_soon_threadsafe(_spawn, context=_dispatch_context)
        else:
            loop.call_soon_threadsafe(_spawn)
        return True
    except RuntimeError:
        logger.debug("Event loop closed, cannot dispatch media-key callback")
        return False
