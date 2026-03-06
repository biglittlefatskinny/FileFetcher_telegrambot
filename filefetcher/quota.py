"""Per-user rolling download quotas for File Fetcher Bot.

Each user gets an independent hourly and daily byte budget implemented as
rolling windows (not fixed clock windows).  This means a user who downloads
200 MB at 10:55 cannot download again until 11:55, regardless of wall-clock
hour boundaries.

All state is in-memory and resets when the bot restarts.  This is intentional
— the bot is designed to be simple and stateless.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, Dict

MB = 1024 * 1024


class QuotaExceeded(Exception):
    def __init__(self, window: str, limit_mb: int, resets_in: float) -> None:
        self.window = window
        self.limit_mb = limit_mb
        self.resets_in = resets_in
        super().__init__(
            f"{window} quota of {limit_mb} MB exceeded; resets in {resets_in:.0f}s"
        )


class _Window:
    """Rolling-window byte counter backed by a timestamp deque."""

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._entries: Deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self._seconds
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()

    def total(self, now: float) -> int:
        self._prune(now)
        return sum(b for _, b in self._entries)

    def add(self, byte_count: int, now: float) -> None:
        self._prune(now)
        self._entries.append((now, byte_count))

    def resets_in(self, now: float) -> float:
        """Seconds until the oldest entry leaves the window."""
        self._prune(now)
        if not self._entries:
            return 0.0
        return max(0.0, (self._entries[0][0] + self._seconds) - now)


class QuotaTracker:
    """Per-user rolling hourly and daily download quotas."""

    def __init__(self, max_hourly_bytes: int, max_daily_bytes: int) -> None:
        self._hourly_limit = max_hourly_bytes
        self._daily_limit = max_daily_bytes
        self._hourly: Dict[int, _Window] = {}
        self._daily: Dict[int, _Window] = {}
        self._lock = asyncio.Lock()

    def _windows(self, user_id: int) -> tuple[_Window, _Window]:
        if user_id not in self._hourly:
            self._hourly[user_id] = _Window(3600)
        if user_id not in self._daily:
            self._daily[user_id] = _Window(86400)
        return self._hourly[user_id], self._daily[user_id]

    async def check(self, user_id: int, byte_count: int) -> None:
        """Raise QuotaExceeded if adding byte_count would breach any limit."""
        async with self._lock:
            h, d = self._windows(user_id)
            now = time.monotonic()
            if h.total(now) + byte_count > self._hourly_limit:
                raise QuotaExceeded(
                    "hourly", self._hourly_limit // MB, h.resets_in(now)
                )
            if d.total(now) + byte_count > self._daily_limit:
                raise QuotaExceeded(
                    "daily", self._daily_limit // MB, d.resets_in(now)
                )

    async def record(self, user_id: int, byte_count: int) -> None:
        """Record a completed download against the user's quota."""
        async with self._lock:
            h, d = self._windows(user_id)
            now = time.monotonic()
            h.add(byte_count, now)
            d.add(byte_count, now)

    async def usage(self, user_id: int) -> dict:
        """Return current usage figures for display in /status."""
        async with self._lock:
            h, d = self._windows(user_id)
            now = time.monotonic()
            return {
                "hourly_used": h.total(now),
                "hourly_limit": self._hourly_limit,
                "hourly_resets_in": h.resets_in(now),
                "daily_used": d.total(now),
                "daily_limit": self._daily_limit,
                "daily_resets_in": d.resets_in(now),
            }
