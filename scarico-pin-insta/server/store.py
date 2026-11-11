"""Memoria temporanea dei link risolti.

Dopo /api/resolve il server deve ricordarsi l'URL diretto del video per
servirlo a /api/file. Tenerlo in RAM e' sufficiente: il sito ha un solo
utente e un solo processo. Al riavvio si perde, e va bene cosi'.
"""

from __future__ import annotations

import threading
import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLStore(Generic[T]):
    def __init__(self, ttl_seconds: int = 7200, max_items: int = 200) -> None:
        self._ttl = ttl_seconds
        self._max = max_items
        self._items: dict[str, tuple[float, T]] = {}
        self._lock = threading.Lock()

    def put(self, key: str, value: T) -> None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            if len(self._items) >= self._max:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                self._items.pop(oldest, None)
            self._items[key] = (now, value)

    def get(self, key: str) -> T | None:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            found = self._items.get(key)
            return found[1] if found else None

    def __len__(self) -> int:
        with self._lock:
            self._purge(time.monotonic())
            return len(self._items)

    def _purge(self, now: float) -> None:
        dead = [k for k, (stamp, _) in self._items.items() if now - stamp > self._ttl]
        for key in dead:
            self._items.pop(key, None)
