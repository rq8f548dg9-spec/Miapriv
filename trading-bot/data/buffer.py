"""Ring buffer in memoria, thread-safe.

Il produttore (il task WebSocket) e il consumatore (feature engine, writer
Parquet) girano su task diversi e, nel caso del writer, possono girare su un
thread diverso. Il buffer e' il punto in cui si incontrano, quindi ogni
accesso passa da un lock.

Capienza fissa: se il consumatore resta indietro si perdono i dati piu'
vecchi, non la memoria del processo. Le perdite vengono contate, perche' un
buffer che scarta in silenzio nasconde un problema di throughput.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")

__all__ = ["RingBuffer"]


class RingBuffer(Generic[T]):
    """Coda circolare a capienza fissa, sicura fra thread."""

    __slots__ = ("_dropped", "_items", "_lock", "_received")

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError(f"capienza {capacity} non positiva")
        self._items: deque[T] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._dropped = 0
        self._received = 0

    @property
    def capacity(self) -> int:
        maxlen = self._items.maxlen
        assert maxlen is not None  # garantito dal costruttore
        return maxlen

    @property
    def dropped(self) -> int:
        """Elementi scartati per sovraccarico. Diverso da zero va investigato."""
        with self._lock:
            return self._dropped

    @property
    def received(self) -> int:
        with self._lock:
            return self._received

    def append(self, item: T) -> None:
        with self._lock:
            if len(self._items) == self._items.maxlen:
                self._dropped += 1
            self._items.append(item)
            self._received += 1

    def extend(self, items: Iterable[T]) -> None:
        for item in items:
            self.append(item)

    def latest(self, count: int = 1) -> list[T]:
        """Ultimi ``count`` elementi, dal piu' vecchio al piu' recente."""
        if count <= 0:
            return []
        with self._lock:
            size = len(self._items)
            if count >= size:
                return list(self._items)
            return [self._items[i] for i in range(size - count, size)]

    def last(self) -> T | None:
        with self._lock:
            return self._items[-1] if self._items else None

    def snapshot(self) -> list[T]:
        """Copia di tutto il contenuto, senza consumarlo."""
        with self._lock:
            return list(self._items)

    def drain(self, max_items: int | None = None) -> list[T]:
        """Estrae e rimuove fino a ``max_items`` elementi, i piu' vecchi prima.

        Usato dal writer Parquet: gli elementi estratti sono di sua proprieta',
        e se la scrittura fallisce tocca a lui non perderli.
        """
        with self._lock:
            if max_items is None or max_items >= len(self._items):
                drained = list(self._items)
                self._items.clear()
                return drained
            return [self._items.popleft() for _ in range(max_items)]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __iter__(self) -> Iterator[T]:
        """Itera su una copia: il produttore puo' scrivere durante l'iterazione."""
        return iter(self.snapshot())
