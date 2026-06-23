"""A small token-bucket rate limiter.

LINE will throttle or block clients that send too fast (``EXCESSIVE_ACCESS`` /
``ABUSE_BLOCK``). Attach a :class:`RateLimiter` to the transport to space out
requests automatically::

    from okline import OkLine
    from okline.ratelimit import RateLimiter
    api = OkLine(access_token="...")
    api.transport.rate_limiter = RateLimiter(rate=5, per=1.0)   # ~5 req/s
"""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Token bucket: allow ``rate`` requests per ``per`` seconds (burst = rate)."""

    def __init__(
        self, rate: float = 5.0, per: float = 1.0, burst: float | None = None
    ) -> None:
        self.rate = float(rate)
        self.per = float(per)
        self.capacity = float(burst if burst is not None else rate)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cost: float = 1.0) -> float:
        """Block until ``cost`` tokens are available; return seconds waited."""
        waited = 0.0
        with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last
                self._last = now
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * (self.rate / self.per)
                )
                if self._tokens >= cost:
                    self._tokens -= cost
                    return waited
                need = (cost - self._tokens) * (self.per / self.rate)
                time.sleep(need)
                waited += need
