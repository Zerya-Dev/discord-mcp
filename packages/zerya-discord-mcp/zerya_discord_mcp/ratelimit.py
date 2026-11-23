"""In-memory token bucket rate limiter (per process)."""

import time


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(per_minute)
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        if len(self._buckets) > 10_000:
            # Drop full buckets so idle keys do not accumulate forever.
            self._buckets = {k: v for k, v in self._buckets.items() if v[0] < self.capacity}
        return True
