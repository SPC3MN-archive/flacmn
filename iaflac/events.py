"""Progress reporting and cancellation shared by the engine and its front ends.

A front end passes `progress(event: dict)` and `cancel() -> bool` callables; the engine calls `progress` with small
dicts ({"stage": "decode", "identifier": ..., "index": 3, "total": 12, ...}) and checks `cancel` between steps and
between download chunks. When `cancel()` turns true the work stops by raising Cancelled; downloads leave their
`.part` files so the next run resumes where this one stopped (that is how pause works).
"""

from __future__ import annotations

from typing import Callable, Optional

Progress = Callable[[dict], None]
CancelCheck = Callable[[], bool]


class Cancelled(Exception):
    pass


def noop(_event: dict) -> None:
    pass


def never() -> bool:
    return False


def check(cancel: Optional[CancelCheck]) -> None:
    if cancel is not None and cancel():
        raise Cancelled()
