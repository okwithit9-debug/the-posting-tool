"""Refuse browser checks while a posting run is active.

- ``<state dir>/.dispatch.lock`` is flock'ed by ``schedule_batch.py``
  dispatch/retry. We try a NON-blocking flock; if it is held, a run is
  active -> refuse. If we get it, we HOLD it for the whole browser check,
  so a dispatch cannot start mid-check (it fails closed with "another
  dispatch is already running" instead of fighting over the one Chrome).
- A non-empty ``.in_flight/`` also means a run is mid-batch -> refuse.
- ``<snapshots>/.sync.lock`` prevents two syncs at once.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Iterator, Optional

try:
    import fcntl  # POSIX (macOS/Linux)
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


class PostingRunActive(RuntimeError):
    pass


def in_flight_busy(in_flight: Optional[Path]) -> bool:
    if not in_flight or not in_flight.is_dir():
        return False
    return any(p.suffix == ".json" for p in in_flight.iterdir())


@contextlib.contextmanager
def hold_flock(path: Path, *, what: str) -> Iterator[None]:
    if fcntl is None:
        raise PostingRunActive("flock unavailable on this OS; refusing browser checks")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    f = open(path, "a")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise PostingRunActive(f"{what} is active ({path.name} held)")
        try:
            yield
        finally:
            if not existed:
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        f.close()


@contextlib.contextmanager
def browser_check_guard(dispatch_lock: Path, in_flight: Optional[Path]) -> Iterator[None]:
    if in_flight_busy(in_flight):
        raise PostingRunActive("posting run active (.in_flight has receipts)")
    with hold_flock(dispatch_lock, what="a posting run (dispatch/retry)"):
        # Re-check after taking the lock (race with a run that just started).
        if in_flight_busy(in_flight):
            raise PostingRunActive("posting run active (.in_flight has receipts)")
        yield
