"""Bounded, per-process rate limiting for the anonymous public mount."""
from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketRateLimiter:
    """Thread-safe token buckets with a bounded LRU client table."""

    def __init__(
        self,
        *,
        requests: int,
        window_seconds: float,
        max_clients: int,
        clock: Callable[[], float] = time.monotonic,
    ):
        if requests < 1 or window_seconds <= 0 or max_clients < 1:
            raise ValueError("rate-limit values must be positive")
        self._capacity = float(requests)
        self._refill_per_second = self._capacity / window_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, client_key: str) -> RateLimitDecision:
        """Consume one token, returning a safe retry delay when exhausted."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.pop(client_key, None)
            if bucket is None:
                if len(self._buckets) >= self._max_clients:
                    self._buckets.popitem(last=False)
                bucket = _Bucket(tokens=self._capacity, updated_at=now)
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(
                    self._capacity,
                    bucket.tokens + elapsed * self._refill_per_second,
                )
                bucket.updated_at = now
            self._buckets[client_key] = bucket

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return RateLimitDecision(allowed=True)

            wait = (1.0 - bucket.tokens) / self._refill_per_second
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=max(1, math.ceil(wait)),
            )
