"""Process-global pacing for the NCBI E-utilities endpoint.

NCBI's limit is per source IP: 3 requests/second without an API key, 10 with
one. At a codeathon that IP is shared by the whole room, so this gate is
deliberately conservative and configurable downward via NCBI_MAX_RPS.

This is a minimum-spacing gate, not a token bucket. A bucket permits a burst
that drains the allowance instantly; measured behavior is that NCBI counts
requests inside a rolling second and 429s the moment you exceed three, so
even spacing is what actually keeps you under.

Three signals are honored, all measured against the live API:

  * HTTP 429 carries ``Retry-After`` (seconds, observed value 2).
  * Successful responses carry ``X-Ratelimit-Remaining``, counting 2 -> 1 -> 0.
    Reaching 0 means the next request in this window will be rejected, so we
    pause proactively instead of spending a request to learn that.
  * ``X-Ratelimit-Limit`` reports the ceiling NCBI thinks we have, which is how
    we notice an API key was rejected and we are really on the keyless limit.
"""

from __future__ import annotations

import os

import anyio

DEFAULT_RPS_KEYLESS = 3.0
DEFAULT_RPS_WITH_KEY = 10.0


def _configured_rps(has_api_key: bool) -> float:
    """Requests per second to target, from the environment or the NCBI default."""
    ceiling = DEFAULT_RPS_WITH_KEY if has_api_key else DEFAULT_RPS_KEYLESS
    raw = os.environ.get("NCBI_MAX_RPS")
    if not raw:
        return ceiling
    try:
        requested = float(raw)
    except ValueError:
        return ceiling
    if requested <= 0:
        return ceiling
    # NCBI_MAX_RPS may only lower the rate. Raising it past what NCBI grants
    # just converts throughput into 429s.
    return min(requested, ceiling)


class RateLimiter:
    """Serializes request starts and spaces them by at least ``1 / rate`` seconds.

    Single-event-loop by construction: every caller must be async, so one
    ``anyio.Lock`` is sufficient and no thread-safe counterpart is needed. See
    the module comment in ``server.py`` for why that invariant matters.
    """

    def __init__(self, has_api_key: bool = False, clock=None, sleep=None) -> None:
        self.rate = _configured_rps(has_api_key)
        self._min_spacing = 1.0 / self.rate
        self._lock = anyio.Lock()
        # Injectable so tests can drive a fake clock instead of sleeping for
        # real; a spacing test against the wall clock is slow and flaky.
        self._clock = clock or anyio.current_time
        self._sleep = sleep or anyio.sleep
        self._next_allowed = 0.0  # monotonic clock reading
        # Set when a 429 tells us to stand down, or when X-Ratelimit-Remaining
        # hits 0 and we decide to wait out the window rather than be rejected.
        self._blocked_until = 0.0

    async def acquire(self) -> None:
        """Block until it is this caller's turn to issue a request."""
        async with self._lock:
            now = self._clock()
            ready_at = max(now, self._next_allowed, self._blocked_until)
            if ready_at > now:
                await self._sleep(ready_at - now)
                now = self._clock()
            # Reserve the next slot while still holding the lock, so concurrent
            # callers queue up spaced rather than all reading the same value.
            self._next_allowed = now + self._min_spacing

    def note_response(self, status_code: int, headers) -> None:
        """Fold a response's rate-limit headers back into the schedule.

        ``headers`` is any case-insensitive mapping (httpx's ``Headers``).
        Safe to call on every response, success or failure.
        """
        if status_code == 429:
            self._back_off(self._retry_after(headers, default=1.0))
            return

        remaining = _int_header(headers, "X-Ratelimit-Remaining")
        if remaining is not None and remaining <= 0:
            # The allowance for this window is spent. Sitting out a full window
            # is cheaper than spending a request to be told 429.
            self._back_off(1.0)

    def note_transport_error(self) -> None:
        """Back off after a connection-level failure, which may itself be throttling."""
        self._back_off(1.0)

    def _back_off(self, seconds: float) -> None:
        candidate = self._clock() + seconds
        self._blocked_until = max(self._blocked_until, candidate)

    @staticmethod
    def _retry_after(headers, default: float) -> float:
        value = _int_header(headers, "Retry-After")
        if value is None or value < 0:
            return default
        # Cap so a hostile or malformed header cannot wedge the server.
        return float(min(value, 60))


def _int_header(headers, name: str) -> int | None:
    try:
        raw = headers.get(name)
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        # Retry-After is also allowed to be an HTTP-date. NCBI sends seconds;
        # if that ever changes, fall back to the caller's default.
        return None
