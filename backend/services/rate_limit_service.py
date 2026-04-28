from __future__ import annotations

import os
import threading
import time
from collections import defaultdict


class InMemoryLoginRateLimiter:
    def __init__(self) -> None:
        self._max_attempts = self._get_env_int("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", 5)
        self._window_seconds = self._get_env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300)
        self._block_seconds = self._get_env_int("LOGIN_RATE_LIMIT_BLOCK_SECONDS", 900)
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._blocked_until: dict[str, float] = {}

    @staticmethod
    def _get_env_int(name: str, default: int) -> int:
        raw_value = os.getenv(name, str(default)).strip()
        try:
            parsed = int(raw_value)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    def get_retry_after_seconds(self, key: str) -> int:
        now = time.time()
        with self._lock:
            blocked_until = self._blocked_until.get(key, 0.0)
            if blocked_until <= now:
                self._blocked_until.pop(key, None)
                return 0
            return max(1, int(blocked_until - now))

    def register_failure(self, key: str) -> int:
        now = time.time()
        with self._lock:
            blocked_until = self._blocked_until.get(key, 0.0)
            if blocked_until > now:
                return max(1, int(blocked_until - now))

            recent_failures = [
                failure_timestamp
                for failure_timestamp in self._failures.get(key, [])
                if now - failure_timestamp <= self._window_seconds
            ]
            recent_failures.append(now)
            self._failures[key] = recent_failures

            if len(recent_failures) >= self._max_attempts:
                until = now + self._block_seconds
                self._blocked_until[key] = until
                self._failures[key] = []
                return max(1, int(self._block_seconds))

            return 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)
