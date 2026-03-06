"""Rate limiting, circuit breaker, and task tracker for Open Sneak Bot.

Components
----------
RateLimiter   – Per-user token-bucket; rejects bursts of requests.
CircuitBreaker – Per-domain failure counter; stops hammering dead sites.
TaskTracker   – Per-user asyncio.Task registry; enables /cancel.

Why these matter for a censorship-bypass bot
---------------------------------------------
* RateLimiter prevents a single user (or small group) from consuming all
  browser/CPU resources and degrading service for everyone else.
* CircuitBreaker avoids wasting Playwright launches on sites that are
  currently unreachable (e.g. temporarily firewalled or down).
* TaskTracker lets users self-service cancel a stuck screenshot without
  waiting for the timeout—important when target sites have long load times.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List

logger = logging.getLogger(__name__)


# ── Exceptions ────────────────────────────────────────────────────────────────

class RateLimitError(Exception):
    """Raised when a user exceeds their per-minute request allowance."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after:.0f}s.")


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open for a domain."""

    def __init__(self, domain: str, reset_in: float) -> None:
        self.domain = domain
        self.reset_in = reset_in
        super().__init__(f"Circuit open for {domain}; resets in {reset_in:.0f}s.")


class QueueFullError(Exception):
    """Raised when the global semaphore backlog is exhausted."""


# ── Token-bucket rate limiter ─────────────────────────────────────────────────

class _Bucket:
    __slots__ = ("rate", "burst", "tokens", "last")

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        self.rate = rate_per_sec
        self.burst = burst
        self.tokens: float = float(burst)
        self.last: float = time.monotonic()

    def try_consume(self) -> tuple[bool, float]:
        """Return (allowed, wait_seconds)."""
        now = time.monotonic()
        self.tokens = min(
            self.burst, self.tokens + (now - self.last) * self.rate
        )
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0
        return False, (1.0 - self.tokens) / self.rate


class RateLimiter:
    def __init__(self, rpm: int, burst: int) -> None:
        self._rate = rpm / 60.0
        self._burst = burst
        self._buckets: Dict[int, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def check(self, user_id: int) -> None:
        """Raise :class:`RateLimitError` if the user is over the limit."""
        async with self._lock:
            bucket = self._buckets.setdefault(
                user_id, _Bucket(self._rate, self._burst)
            )
            allowed, wait = bucket.try_consume()
        if not allowed:
            raise RateLimitError(retry_after=wait)

    async def purge_stale(self) -> None:
        """Drop buckets that haven't been touched in the last 10 minutes."""
        cutoff = time.monotonic() - 600.0
        async with self._lock:
            stale = [uid for uid, b in self._buckets.items() if b.last < cutoff]
            for uid in stale:
                del self._buckets[uid]


# ── Circuit breaker ───────────────────────────────────────────────────────────

class CircuitBreaker:
    """Per-domain failure-count circuit breaker.

    Counts failures within a rolling *reset_seconds* window.  Once
    *threshold* failures accumulate the circuit opens for *reset_seconds*.
    """

    def __init__(self, threshold: int, reset_seconds: float) -> None:
        self.threshold = threshold
        self.reset_seconds = reset_seconds
        self._failures: Dict[str, List[float]] = {}
        self._open_until: Dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def check(self, domain: str) -> None:
        """Raise :class:`CircuitOpenError` if the circuit is open."""
        async with self._lock:
            until = self._open_until.get(domain)
            if until is None:
                return
            now = time.monotonic()
            if now < until:
                raise CircuitOpenError(domain, reset_in=until - now)
            # Half-open: allow one probe through; clear state
            del self._open_until[domain]
            self._failures.pop(domain, None)

    async def record_failure(self, domain: str) -> None:
        async with self._lock:
            now = time.monotonic()
            times = self._failures.setdefault(domain, [])
            times.append(now)
            # Keep only failures within the rolling window
            self._failures[domain] = [
                t for t in times if now - t < self.reset_seconds
            ]
            if len(self._failures[domain]) >= self.threshold:
                self._open_until[domain] = now + self.reset_seconds
                logger.warning(
                    "event=circuit_opened domain=%s failures=%d",
                    domain,
                    len(self._failures[domain]),
                )

    async def record_success(self, domain: str) -> None:
        async with self._lock:
            self._failures.pop(domain, None)
            self._open_until.pop(domain, None)

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            return {
                "open_circuits": len(self._open_until),
                "tracked_domains": len(self._failures),
            }


# ── Task tracker (for /cancel) ────────────────────────────────────────────────

class TaskTracker:
    """Register the asyncio.Task for each user's running operation.

    Allows /cancel to interrupt a long screenshot or fetch by calling
    ``Task.cancel()``, which injects CancelledError at the next await.
    """

    def __init__(self) -> None:
        self._tasks: Dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def register(self, user_id: int, task: asyncio.Task) -> None:
        """Register *task* for *user_id*, cancelling any prior running task."""
        async with self._lock:
            old = self._tasks.get(user_id)
            if old is not None and not old.done():
                old.cancel()
            self._tasks[user_id] = task

    async def cancel(self, user_id: int) -> bool:
        """Cancel the registered task for *user_id*. Returns True if found."""
        async with self._lock:
            task = self._tasks.pop(user_id, None)
            if task is not None and not task.done():
                task.cancel()
                return True
            return False

    async def unregister(self, user_id: int) -> None:
        async with self._lock:
            self._tasks.pop(user_id, None)

    @property
    def active_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())
