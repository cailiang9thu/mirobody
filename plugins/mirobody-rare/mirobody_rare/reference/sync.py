"""Run coroutines against the asyncpg pool from synchronous code (the coding pipeline and
the threaded shim). One private event loop on one thread owns the pool; callers block on a
future. Inside an already-running loop use the async API directly."""

from __future__ import annotations

import asyncio
import threading

_LOOP: asyncio.AbstractEventLoop | None = None
_LOCK = threading.Lock()


def _loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    with _LOCK:
        if _LOOP is None:
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="mirobody-rare-pg", daemon=True).start()
        return _LOOP


def run(coro):
    return asyncio.run_coroutine_threadsafe(coro, _loop()).result()
