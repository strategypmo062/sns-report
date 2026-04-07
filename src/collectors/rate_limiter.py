"""Simple sleep-based rate limiter for web scraping."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_sec: float):
        self._min_interval = min_interval_sec
        self._last_request_time = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()
