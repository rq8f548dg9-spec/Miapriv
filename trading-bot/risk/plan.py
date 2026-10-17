"""Il piano con cui una posizione e' stata aperta: stop e target in prezzo.

Vive qui, fuori dal runner, perche' lo usano sia il bot che opera sia il
backtest. Se lo eseguisse solo il primo, la simulazione misurerebbe una
gestione del rischio diversa da quella vera, ed e' esattamente il genere di
differenza che rende inutile un backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.types import Position, TimestampMs
from signals.base import TradePlan

__all__ = ["PianoDiUscita", "piano_di_uscita"]


@dataclass(frozen=True, slots=True)
class PianoDiUscita:
    """Stop e target di una posizione aperta, in prezzo.

    Il generatore dichiara un piano in punti base e il risk manager lo usa
    per dimensionare con Kelly. Se poi nessuno lo esegue, la scommessa su
    cui si e' dimensionato non e' quella che si sta correndo: la posizione
    esce al cambio del punteggio, che puo' arrivare molto oltre lo stop.
    """

    symbol: str
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    long: bool
    created_ms: TimestampMs

    def violato_da(self, price: Decimal) -> str | None:
        """Nome del livello raggiunto da questo prezzo, o None."""
        if self.long:
            if price <= self.stop_price:
                return "stop"
            if price >= self.target_price:
                return "target"
            return None
        if price >= self.stop_price:
            return "stop"
        if price <= self.target_price:
            return "target"
        return None

    def to_dict(self) -> dict[str, str]:
        return {
            "entry_price": str(self.entry_price),
            "stop_price": str(self.stop_price),
            "target_price": str(self.target_price),
            "long": "1" if self.long else "0",
            "created_ms": str(self.created_ms),
        }

    @classmethod
    def from_dict(cls, symbol: str, payload: dict[str, str]) -> PianoDiUscita:
        return cls(
            symbol=symbol,
            entry_price=Decimal(payload["entry_price"]),
            stop_price=Decimal(payload["stop_price"]),
            target_price=Decimal(payload["target_price"]),
            long=payload["long"] == "1",
            created_ms=int(payload["created_ms"]),
        )


def piano_di_uscita(
    symbol: str, position: Position, plan: TradePlan, now_ms: TimestampMs
) -> PianoDiUscita:
    """Traduce un piano in punti base nei prezzi di stop e target.

    I livelli si calcolano sul prezzo di carico effettivo, non sul prezzo
    visto quando si e' deciso: fra i due c'e' lo slippage, e lo stop va dove
    protegge davvero.
    """
    entry = position.average_entry_price
    long = position.quantity > 0
    direzione = Decimal(1) if long else Decimal(-1)
    stop_frazione = Decimal(str(plan.stop_bps)) / Decimal(10_000)
    target_frazione = Decimal(str(plan.take_profit_bps)) / Decimal(10_000)
    return PianoDiUscita(
        symbol=symbol,
        entry_price=entry,
        stop_price=entry * (Decimal(1) - direzione * stop_frazione),
        target_price=entry * (Decimal(1) + direzione * target_frazione),
        long=long,
        created_ms=now_ms,
    )
