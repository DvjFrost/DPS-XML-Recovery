"""Dependency-free cooperative cancellation primitives."""

from __future__ import annotations

import threading


class UserRequestedStop(RuntimeError):
    """Cooperative cancellation observed at a durable progress checkpoint."""


class StopToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request_stop(self) -> None:
        self._event.set()

    def is_requested(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_requested(self) -> None:
        if self._event.is_set():
            raise UserRequestedStop("cooperative stop requested")


__all__ = ["StopToken", "UserRequestedStop"]
