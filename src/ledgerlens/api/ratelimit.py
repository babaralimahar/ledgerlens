"""Per-API-key sliding-window rate limit (in process memory).

Good enough for one instance; with several replicas it becomes per-replica. The documented
upgrade is a shared counter (Postgres or Redis)."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.api.errors import DomainError


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = max(1, int(self.window_s - (now - hits[0])) + 1)
            raise DomainError(
                429,
                "rate-limited",
                "Too many uploads",
                f"limit {self.limit}/min; retry in {retry}s",
            )
        hits.append(now)
