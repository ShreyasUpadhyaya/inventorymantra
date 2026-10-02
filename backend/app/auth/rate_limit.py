"""In-memory login throttle: N failures per (email, client IP) within a sliding window.

Per-process only, which is fine for the single-instance demo; a multi-instance deploy would
move this to Redis or the DB.
"""

import threading
import time
from collections import defaultdict, deque

from app.core.config import get_settings


class LoginRateLimiter:
    def __init__(self) -> None:
        self._failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: tuple[str, str], now: float) -> deque[float]:
        window = get_settings().login_window_seconds
        hits = self._failures[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        return hits

    def is_blocked(self, email: str, ip: str) -> bool:
        with self._lock:
            hits = self._prune((email, ip), time.monotonic())
            return len(hits) >= get_settings().login_max_failures

    def record_failure(self, email: str, ip: str) -> None:
        with self._lock:
            self._prune((email, ip), time.monotonic()).append(time.monotonic())

    def reset(self, email: str, ip: str) -> None:
        with self._lock:
            self._failures.pop((email, ip), None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter()
