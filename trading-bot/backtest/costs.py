"""Modello dei costi di transazione.

Principio 5 del progetto: i costi si simulano sempre. Un backtest "al netto
delle fee" non e' un backtest ottimista, e' un backtest sbagliato: su ordini
da 5 unita' con fee taker a 10 bps, una strategia che guadagna 8 bps per
operazione perde denaro a ogni singolo giro.

Tre componenti, tutte parametriche:

* **fee**: percentuale sul controvalore, diversa fra maker e taker;
* **slippage**: scostamento fra il prezzo visto e il prezzo ottenuto,
  modellato come ``base + impatto * sqrt(size / profondita')``. La radice
  riflette l'impatto concavo osservato sui book reali: raddoppiare la size
  non raddoppia l'impatto;
* **latenza**: fra la decisione e l'esecuzione passa del tempo, e il prezzo
  si muove. Nel motore di backtest questo e' reso strutturalmente, eseguendo
  sulla barra successiva a quella della decisione.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from config.schema import CostsConfig
from core.types import Side

__all__ = ["CostModel", "ExecutionCost"]


@dataclass(frozen=True, slots=True)
class ExecutionCost:
    """Scomposizione del costo di una singola esecuzione."""

    fill_price: Decimal
    reference_price: Decimal
    fee: Decimal
    slippage_bps: float
    fee_bps: float
    is_maker: bool

    @property
    def slippage_cost(self) -> Decimal:
        """Costo dello scostamento, in valuta quote."""
        return abs(self.fill_price - self.reference_price)

    @property
    def total_bps(self) -> float:
        return self.fee_bps + self.slippage_bps


@dataclass(frozen=True)
class CostModel:
    """Calcolo dei costi a partire dalla configurazione."""

    config: CostsConfig

    def slippage_bps(self, notional: Decimal, available_depth: Decimal | None = None) -> float:
        """Scostamento atteso in punti base, crescente con la size."""
        depth = available_depth if available_depth and available_depth > 0 else (
            self.config.reference_depth_quote
        )
        ratio = float(notional / depth)
        return self.config.base_slippage_bps + self.config.impact_coefficient_bps * math.sqrt(
            max(0.0, ratio)
        )

    def fee_bps(self, *, is_maker: bool) -> float:
        return self.config.maker_fee_bps if is_maker else self.config.taker_fee_bps

    def round_trip_bps(self, notional: Decimal, *, is_maker: bool = False) -> float:
        """Costo di un giro completo (entrata + uscita), in bps.

        E' il numero che un segnale deve battere per valere la pena, ed e'
        quello che il risk manager riceve come ``estimated_cost_bps``.
        """
        return 2.0 * (self.fee_bps(is_maker=is_maker) + self.slippage_bps(notional))

    def apply(
        self,
        *,
        side: Side,
        reference_price: Decimal,
        quantity: Decimal,
        is_maker: bool = False,
        available_depth: Decimal | None = None,
    ) -> ExecutionCost:
        """Prezzo effettivo e commissione di un'esecuzione.

        Lo slippage va sempre contro chi esegue: chi compra paga di piu', chi
        vende incassa di meno. Un modello che lo rendesse simmetrico in media
        regalerebbe alla strategia meta' dei suoi costi.
        """
        if reference_price <= 0:
            raise ValueError(f"prezzo di riferimento {reference_price} non positivo")
        if quantity <= 0:
            raise ValueError(f"quantita' {quantity} non positiva")

        notional = reference_price * quantity
        slip_bps = self.slippage_bps(notional, available_depth)
        slip_factor = Decimal(str(slip_bps)) / Decimal(10_000)
        direction = Decimal(1) if side is Side.BUY else Decimal(-1)
        fill_price = reference_price * (Decimal(1) + direction * slip_factor)

        fee_rate = Decimal(str(self.fee_bps(is_maker=is_maker))) / Decimal(10_000)
        fee = fill_price * quantity * fee_rate

        return ExecutionCost(
            fill_price=fill_price,
            reference_price=reference_price,
            fee=fee,
            slippage_bps=slip_bps,
            fee_bps=self.fee_bps(is_maker=is_maker),
            is_maker=is_maker,
        )
