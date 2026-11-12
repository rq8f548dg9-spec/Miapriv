"""Astrazione del tempo.

Il backtest e il live devono eseguire lo STESSO codice di decisione. L'unico
modo per ottenerlo e' che nessun modulo chiami mai ``time.time()``,
``datetime.now()`` o ``asyncio.sleep()`` direttamente: tutto passa da un
``Clock`` iniettato.

Due implementazioni:

* ``LiveClock``   - orologio di sistema, usato in testnet e live.
* ``SimulatedClock`` - tempo virtuale pilotato dal backtest engine. Il tempo
  avanza solo quando l'engine lo fa avanzare, mai da solo e mai all'indietro.

``monotonic_ns`` e' separato da ``now_ms``: serve a misurare latenze e deve
essere immune agli aggiustamenti NTP dell'orologio di sistema.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from core.types import TimestampMs, ms_to_datetime

__all__ = ["Clock", "LiveClock", "SimulatedClock"]


@runtime_checkable
class Clock(Protocol):
    """Interfaccia unica del tempo. Ogni componente riceve questo, non il modulo time."""

    def now_ms(self) -> TimestampMs:
        """Istante corrente in ms epoch UTC."""
        ...

    def now(self) -> datetime:
        """Istante corrente come ``datetime`` aware UTC."""
        ...

    def monotonic_ns(self) -> int:
        """Contatore monotono in ns, per misurare durate. Non e' un orario."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Attende ``seconds``. Nel backtest avanza il tempo virtuale."""
        ...


class LiveClock:
    """Orologio reale. Usato in ``testnet`` e ``live``."""

    __slots__ = ()

    def now_ms(self) -> TimestampMs:
        return int(time.time() * 1000)

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"sleep negativo: {seconds}")
        await asyncio.sleep(seconds)


class SimulatedClock:
    """Tempo virtuale deterministico per il backtest.

    Il tempo avanza in due modi, entrambi espliciti:

    * ``advance_to(ts_ms)`` - chiamato dall'engine quando processa un evento;
    * ``sleep(seconds)``    - un task che dorme cede il controllo e viene
      risvegliato quando il tempo virtuale raggiunge la sua scadenza.

    Il tempo non torna mai indietro: ``advance_to`` con un istante passato e'
    un errore, non un no-op silenzioso. Un backtest che prova a tornare
    indietro sta quasi sempre sbirciando il futuro da qualche altra parte.
    """

    __slots__ = ("_counter", "_monotonic_ns", "_now_ms", "_waiters")

    def __init__(self, start_ms: TimestampMs = 0) -> None:
        self._now_ms: TimestampMs = start_ms
        self._monotonic_ns: int = 0
        # heap di (scadenza_ms, seq, future) per i task in sleep
        self._waiters: list[tuple[TimestampMs, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    def now_ms(self) -> TimestampMs:
        return self._now_ms

    def now(self) -> datetime:
        return ms_to_datetime(self._now_ms)

    def monotonic_ns(self) -> int:
        return self._monotonic_ns

    def advance_to(self, ts_ms: TimestampMs) -> None:
        """Porta il tempo virtuale a ``ts_ms`` e risveglia gli sleep scaduti."""
        if ts_ms < self._now_ms:
            raise ValueError(
                f"tempo all'indietro: {ts_ms} < {self._now_ms}. "
                "Nel backtest indica quasi sempre un look-ahead."
            )
        self._monotonic_ns += (ts_ms - self._now_ms) * 1_000_000
        self._now_ms = ts_ms
        self._release_due()

    def advance_by(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError(f"avanzamento negativo: {milliseconds} ms")
        self.advance_to(self._now_ms + milliseconds)

    async def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"sleep negativo: {seconds}")
        if seconds == 0:
            await asyncio.sleep(0)
            return
        deadline = self._now_ms + int(seconds * 1000)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (deadline, next(self._counter), future))
        await future

    @property
    def pending_wakeups(self) -> int:
        """Numero di task in attesa. L'engine lo usa per sapere se puo' chiudere."""
        return len(self._waiters)

    def next_wakeup_ms(self) -> TimestampMs | None:
        """Prima scadenza in sospeso, se esiste."""
        return self._waiters[0][0] if self._waiters else None

    def _release_due(self) -> None:
        while self._waiters and self._waiters[0][0] <= self._now_ms:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                future.set_result(None)
