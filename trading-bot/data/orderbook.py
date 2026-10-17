"""Ricostruzione del book L2 dal feed differenziale di Binance.

Il flusso ``@depth`` manda differenze, non stati. Ricostruire il book significa
applicarle a uno snapshot REST nell'ordine giusto, e accorgersi quando se ne
perde una: un book con un buco e' un book sbagliato, e un book sbagliato
produce segnali sbagliati con la massima confidenza.

Procedura (documentazione Binance Spot):

1. si aprono e si accumulano gli eventi del flusso;
2. si scarica lo snapshot REST con il suo ``lastUpdateId``;
3. si scartano gli eventi con ``u <= lastUpdateId``;
4. il primo evento valido deve soddisfare ``U <= lastUpdateId + 1 <= u``;
5. ogni evento successivo deve avere ``U == u_precedente + 1``.

Se il passo 5 fallisce c'e' un buco di sequenza: il book viene marcato come
non valido e serve un nuovo snapshot. Non si interpola, non si "tira avanti".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from core.types import BookLevel, OrderBookSnapshot, TimestampMs

__all__ = ["ApplyResult", "DepthUpdate", "OrderBookState"]


@dataclass(frozen=True, slots=True)
class DepthUpdate:
    """Evento differenziale. Una quantita' pari a zero cancella il livello."""

    symbol: str
    first_update_id: int  # campo "U"
    final_update_id: int  # campo "u"
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    event_time_ms: TimestampMs


class ApplyResult(StrEnum):
    APPLIED = "APPLIED"
    #: Evento anteriore allo snapshot: scartato senza conseguenze.
    DROPPED_OLD = "DROPPED_OLD"
    #: Nessuno snapshot ancora applicato: l'evento resta in attesa.
    BUFFERED = "BUFFERED"
    #: Buco di sequenza: il book non e' piu' attendibile.
    GAP = "GAP"


@dataclass
class OrderBookState:
    """Book L2 di un simbolo, ricostruito in streaming."""

    symbol: str
    max_depth: int = 50
    _bids: dict[Decimal, Decimal] = field(default_factory=dict, repr=False)
    _asks: dict[Decimal, Decimal] = field(default_factory=dict, repr=False)
    last_update_id: int = 0
    last_event_ms: TimestampMs = 0
    _synced: bool = False
    _pending: list[DepthUpdate] = field(default_factory=list, repr=False)
    gaps_detected: int = 0

    @property
    def is_synced(self) -> bool:
        """Vero solo se il book e' allineato: finche' e' falso non si decide."""
        return self._synced

    def apply_snapshot(
        self,
        *,
        last_update_id: int,
        bids: tuple[tuple[Decimal, Decimal], ...],
        asks: tuple[tuple[Decimal, Decimal], ...],
        ts_ms: TimestampMs,
    ) -> int:
        """Installa uno snapshot REST e riapplica gli eventi in attesa.

        Restituisce quanti eventi accumulati sono stati applicati.
        """
        self._bids = {price: qty for price, qty in bids if qty > 0}
        self._asks = {price: qty for price, qty in asks if qty > 0}
        self.last_update_id = last_update_id
        self.last_event_ms = ts_ms
        self._synced = True

        pending, self._pending = self._pending, []
        applied = 0
        for update in pending:
            if self.apply(update) is ApplyResult.APPLIED:
                applied += 1
        return applied

    def apply(self, update: DepthUpdate) -> ApplyResult:
        """Applica un evento differenziale rispettando la sequenza."""
        if update.symbol != self.symbol:
            raise ValueError(f"evento su {update.symbol} applicato al book {self.symbol}")

        if not self._synced:
            self._pending.append(update)
            return ApplyResult.BUFFERED

        if update.final_update_id <= self.last_update_id:
            return ApplyResult.DROPPED_OLD

        # Primo evento dopo lo snapshot: deve coprire lastUpdateId + 1.
        expected = self.last_update_id + 1
        if update.first_update_id > expected:
            # Manca almeno un evento: il book non e' piu' ricostruibile.
            self.gaps_detected += 1
            self._synced = False
            self._pending = [update]
            return ApplyResult.GAP

        for price, quantity in update.bids:
            self._set_level(self._bids, price, quantity)
        for price, quantity in update.asks:
            self._set_level(self._asks, price, quantity)

        self.last_update_id = update.final_update_id
        self.last_event_ms = update.event_time_ms
        return ApplyResult.APPLIED

    def invalidate(self, reason: str = "") -> None:
        """Marca il book come non attendibile: serve un nuovo snapshot.

        Chiamata dall'ingest dopo una riconnessione: gli eventi persi mentre
        la connessione era giu' non si recuperano.
        """
        self._synced = False
        self._pending.clear()
        self.last_update_id = 0

    def snapshot(self, ts_ms: TimestampMs | None = None) -> OrderBookSnapshot | None:
        """Fotografia ordinata dei primi ``max_depth`` livelli.

        Restituisce ``None`` se il book non e' allineato: meglio nessun dato
        che un dato sbagliato.
        """
        if not self._synced:
            return None
        bids = sorted(self._bids.items(), key=lambda item: item[0], reverse=True)
        asks = sorted(self._asks.items(), key=lambda item: item[0])
        return OrderBookSnapshot(
            symbol=self.symbol,
            last_update_id=self.last_update_id,
            bids=tuple(BookLevel(p, q) for p, q in bids[: self.max_depth]),
            asks=tuple(BookLevel(p, q) for p, q in asks[: self.max_depth]),
            ts_ms=ts_ms if ts_ms is not None else self.last_event_ms,
        )

    @staticmethod
    def _set_level(side: dict[Decimal, Decimal], price: Decimal, quantity: Decimal) -> None:
        if quantity <= 0:
            side.pop(price, None)
        else:
            side[price] = quantity
