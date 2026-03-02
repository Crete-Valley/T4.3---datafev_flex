"""Thread-safe TTL cache used by the API between Stage-1 and Stage-2 calls.

Design rationale
----------------
The API workflow is intentionally split into two requests. Stage-1 artifacts can
be large and expensive to recompute, so they are stored in-memory for a short
window. This avoids persistent DB dependencies while still supporting a
stateful two-step API interaction.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any
from uuid import uuid4


class TTLObjectCache:
    """Thread-safe in-memory TTL cache for short-lived workflow artifacts."""

    def __init__(self, ttl_seconds: int = 900, max_items: int = 128):
        """Initialize cache constraints.

        Parameters
        ----------
        ttl_seconds : int
            Lifetime of each record in seconds.
        max_items : int
            Maximum number of entries to retain.
        """
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def put(self, value: Any) -> str:
        """Insert value and return generated cache key.

        Side Effects
        ------------
        - Mutates internal ordered store.
        - Evicts expired and overflow items.
        """
        key = uuid4().hex
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._evict_expired_locked()
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            self._evict_overflow_locked()
        return key

    def put_with_key(self, key: str, value: Any) -> str:
        """Insert value under caller-provided cache key.

        Parameters
        ----------
        key : str
            Cache key to use (for example a pre-generated ``stage1_id``).
        value : Any
            Value to store.

        Returns
        -------
        str
            The same key passed by caller.

        Side Effects
        ------------
        - Mutates internal ordered store.
        - Evicts expired and overflow items.
        """
        if key is None or str(key).strip() == "":
            raise ValueError("Cache key must be a non-empty string.")
        key = str(key).strip()
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._evict_expired_locked()
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            self._evict_overflow_locked()
        return key

    def get(self, key: str) -> Any | None:
        """Retrieve value if key exists and is not expired."""
        with self._lock:
            self._evict_expired_locked()
            item = self._store.get(key)
            if item is None:
                return None

            expires_at, value = item
            if expires_at <= time.time():
                self._store.pop(key, None)
                return None

            self._store.move_to_end(key)
            return value

    def delete(self, key: str) -> None:
        """Remove key if present."""
        with self._lock:
            self._store.pop(key, None)

    def size(self) -> int:
        """Return current item count after pruning expired entries."""
        with self._lock:
            self._evict_expired_locked()
            return len(self._store)

    def _evict_expired_locked(self) -> None:
        """Drop expired keys.

        Notes
        -----
        Caller must hold ``self._lock``.
        """
        now = time.time()
        expired_keys = [k for k, (exp, _) in self._store.items() if exp <= now]
        for k in expired_keys:
            self._store.pop(k, None)

    def _evict_overflow_locked(self) -> None:
        """Drop oldest keys until store size is within `max_items`.

        Notes
        -----
        Caller must hold ``self._lock``.
        """
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)
