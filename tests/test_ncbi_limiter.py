"""Rate limiter, driven by a fake clock.

No network and no real sleeping: a spacing test against the wall clock is slow
and flaky, and the thing under test is arithmetic on timestamps.
"""

import pytest
from ncbi_lib.limiter import RateLimiter


class FakeClock:
    """A clock that only moves when the limiter asks to sleep."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(has_api_key: bool = False) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return (
        RateLimiter(has_api_key=has_api_key, clock=clock.time, sleep=clock.sleep),
        clock,
    )


async def test_first_request_does_not_wait():
    limiter, clock = make()
    await limiter.acquire()
    assert clock.sleeps == []


async def test_requests_are_spaced_by_one_third_of_a_second():
    limiter, clock = make()
    for _ in range(4):
        await limiter.acquire()
    assert len(clock.sleeps) == 3
    assert all(abs(s - 1 / 3) < 1e-9 for s in clock.sleeps)


async def test_three_requests_fit_in_one_second():
    """The actual contract: never more than 3 starts per rolling second."""
    limiter, clock = make()
    start = clock.now
    for _ in range(3):
        await limiter.acquire()
    assert clock.now - start < 1.0


async def test_api_key_raises_the_rate_to_ten():
    limiter, clock = make(has_api_key=True)
    await limiter.acquire()
    await limiter.acquire()
    assert abs(clock.sleeps[0] - 0.1) < 1e-9


async def test_429_retry_after_is_honored():
    limiter, clock = make()
    await limiter.acquire()
    limiter.note_response(429, {"Retry-After": "2"})
    await limiter.acquire()
    assert clock.sleeps[-1] == pytest.approx(2.0, abs=0.01)


async def test_exhausted_allowance_backs_off_before_being_rejected():
    """X-Ratelimit-Remaining: 0 on a SUCCESS means the next call would 429.

    Waiting out the window is cheaper than spending a request to find out.
    """
    limiter, clock = make()
    await limiter.acquire()
    limiter.note_response(200, {"X-Ratelimit-Remaining": "0"})
    await limiter.acquire()
    assert clock.sleeps[-1] == pytest.approx(1.0, abs=0.01)


async def test_remaining_above_zero_does_not_back_off():
    limiter, clock = make()
    await limiter.acquire()
    limiter.note_response(200, {"X-Ratelimit-Remaining": "2"})
    await limiter.acquire()
    assert clock.sleeps[-1] == pytest.approx(1 / 3, abs=0.01)


async def test_missing_retry_after_falls_back_to_one_second():
    limiter, clock = make()
    await limiter.acquire()
    limiter.note_response(429, {})
    await limiter.acquire()
    assert clock.sleeps[-1] == pytest.approx(1.0, abs=0.01)


async def test_absurd_retry_after_is_capped():
    """A malformed header must not wedge the server for hours."""
    limiter, clock = make()
    await limiter.acquire()
    limiter.note_response(429, {"Retry-After": "99999"})
    await limiter.acquire()
    assert clock.sleeps[-1] <= 60.0


async def test_http_date_retry_after_does_not_crash():
    limiter, clock = make()
    await limiter.acquire()
    limiter.note_response(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    await limiter.acquire()
    assert clock.sleeps[-1] == pytest.approx(1.0, abs=0.01)


def test_env_can_only_lower_the_rate(monkeypatch):
    """Raising NCBI_MAX_RPS past what NCBI grants just converts throughput
    into 429s, so the ceiling wins."""
    monkeypatch.setenv("NCBI_MAX_RPS", "1")
    assert RateLimiter().rate == 1.0

    monkeypatch.setenv("NCBI_MAX_RPS", "100")
    assert RateLimiter().rate == 3.0


def test_garbage_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("NCBI_MAX_RPS", "fast")
    assert RateLimiter().rate == 3.0
