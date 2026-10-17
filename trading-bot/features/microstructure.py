"""Feature di microstruttura: order flow, book imbalance, microprice, spread.

Sono le uniche feature che a questa frequenza hanno una qualche speranza di
contenere informazione predittiva, e sono anche le piu' facili da calcolare
male. Due errori tipici, entrambi evitati qui:

* usare un book non allineato (dopo un buco di sequenza) come se fosse valido;
* calcolare l'order flow su una finestra che include il trade che si sta
  cercando di prevedere.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from core.types import OrderBookSnapshot, TimestampMs, Trade

__all__ = ["OrderFlowImbalance", "book_features"]


@dataclass
class OrderFlowImbalance:
    """Sbilanciamento del flusso ordini su piu' finestre temporali.

    Per ogni finestra: ``(volume aggressivo in acquisto - in vendita) /
    volume totale``, in ``[-1, +1]``. Il lato aggressore si deduce dal flag
    ``is_buyer_maker`` del trade pubblico: se il compratore era il maker,
    allora chi ha consumato liquidita' e' il venditore.

    Le finestre sono temporali, non a conteggio: in un minuto calmo cinque
    trade sono un'informazione diversa da cinque trade in un secondo.
    """

    windows_ms: tuple[int, ...]
    _trades: deque[tuple[TimestampMs, Decimal, int]] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        if not self.windows_ms:
            raise ValueError("serve almeno una finestra")

    def update(self, trade: Trade) -> None:
        self._trades.append((trade.ts_ms, trade.quantity, trade.aggressor_side.sign))
        self._evict(trade.ts_ms)

    def value(self, window_ms: int, now_ms: TimestampMs) -> float | None:
        """OFI sulla finestra che termina in ``now_ms``.

        ``None`` quando nella finestra non c'e' stato nessun trade: un mercato
        fermo non ha sbilanciamento zero, semplicemente non ha informazione.
        """
        cutoff = now_ms - window_ms
        signed = Decimal(0)
        total = Decimal(0)
        for ts_ms, quantity, sign in reversed(self._trades):
            if ts_ms <= cutoff:
                break
            if ts_ms > now_ms:
                # Anti-look-ahead: un trade successivo all'istante di decisione
                # non e' osservabile, anche se e' gia' nel buffer.
                continue
            signed += quantity * sign
            total += quantity
        if total == 0:
            return None
        return float(signed / total)

    def features(self, now_ms: TimestampMs, prefix: str = "ofi") -> dict[str, float]:
        return {
            f"{prefix}_{window // 1000}s": value
            for window in self.windows_ms
            if (value := self.value(window, now_ms)) is not None
        }

    def trade_count(self, window_ms: int, now_ms: TimestampMs) -> int:
        cutoff = now_ms - window_ms
        return sum(1 for ts_ms, _, _ in self._trades if cutoff < ts_ms <= now_ms)

    def _evict(self, now_ms: TimestampMs) -> None:
        horizon = now_ms - max(self.windows_ms)
        while self._trades and self._trades[0][0] <= horizon:
            self._trades.popleft()


def book_features(
    book: OrderBookSnapshot | None, levels: int, prefix: str = "book"
) -> dict[str, float]:
    """Feature derivate dal book.

    Restituisce un dizionario vuoto se il book e' assente, non allineato o
    incrociato: un book incrociato (bid >= ask) e' il sintomo di una
    ricostruzione sbagliata, e le sue feature sarebbero rumore travestito
    da segnale.
    """
    if book is None or book.is_crossed():
        return {}
    best_bid, best_ask = book.best_bid, book.best_ask
    mid = book.mid_price
    if best_bid is None or best_ask is None or mid is None or mid <= 0:
        return {}

    top_bids = book.bids[:levels]
    top_asks = book.asks[:levels]
    bid_depth = sum((level.notional for level in top_bids), Decimal(0))
    ask_depth = sum((level.notional for level in top_asks), Decimal(0))
    total_depth = bid_depth + ask_depth

    features: dict[str, float] = {
        f"{prefix}_spread_bps": float((best_ask.price - best_bid.price) / mid) * 10_000.0,
        f"{prefix}_depth_quote": float(total_depth),
        f"{prefix}_levels": float(min(len(top_bids), len(top_asks))),
    }

    if total_depth > 0:
        features[f"{prefix}_imbalance"] = float((bid_depth - ask_depth) / total_depth)

    top_total = best_bid.quantity + best_ask.quantity
    if top_total > 0:
        features[f"{prefix}_top_imbalance"] = float(
            (best_bid.quantity - best_ask.quantity) / top_total
        )

    microprice = book.microprice
    if microprice is not None:
        # Scostamento del fair value dal mid: positivo = pressione in acquisto.
        features[f"{prefix}_microprice_dev_bps"] = float((microprice - mid) / mid) * 10_000.0

    return features
