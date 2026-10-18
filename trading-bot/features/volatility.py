"""Volatilita' realizzata multi-finestra e ATR.

Serve a due cose diverse, entrambe necessarie: normalizzare i segnali (un
movimento di 20 bps non significa niente finche' non si sa se la volatilita'
tipica e' 5 o 50 bps) e dimensionare stop e take profit, che a loro volta
determinano il payoff ratio con cui Kelly calcola la size.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from core.types import Candle

__all__ = ["AverageTrueRange", "RealizedVolatility"]


@dataclass
class RealizedVolatility:
    """Deviazione standard dei rendimenti logaritmici su piu' finestre.

    Lavora su rendimenti log perche' sono additivi nel tempo e simmetrici
    fra salita e discesa: con i rendimenti semplici un +10% seguito da -10%
    sembrerebbe neutro mentre non lo e'.
    """

    windows: tuple[int, ...]
    _returns: deque[float] = field(default_factory=deque, repr=False)
    _last_close: Decimal | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("serve almeno una finestra")
        self._returns = deque(maxlen=max(self.windows))

    def update(self, close: Decimal) -> None:
        """Registra una chiusura. Da chiamare solo su candele chiuse."""
        if close <= 0:
            raise ValueError(f"prezzo di chiusura {close} non positivo")
        if self._last_close is not None:
            self._returns.append(math.log(float(close / self._last_close)))
        self._last_close = close

    def value(self, window: int) -> float | None:
        """Volatilita' per barra sulla finestra, o ``None`` se i dati non bastano.

        Restituire ``None`` invece di un numero calcolato su tre osservazioni
        e' deliberato: una stima senza dati e' peggio di nessuna stima, perche'
        a valle sembra un dato valido.
        """
        if window not in self.windows:
            raise ValueError(f"finestra {window} non configurata: {self.windows}")
        if len(self._returns) < window:
            return None
        sample = list(self._returns)[-window:]
        mean = sum(sample) / window
        variance = sum((value - mean) ** 2 for value in sample) / (window - 1)
        return math.sqrt(variance)

    def features(self, prefix: str = "vol") -> dict[str, float]:
        return {
            f"{prefix}_{window}": value
            for window in self.windows
            if (value := self.value(window)) is not None
        }

    @property
    def samples(self) -> int:
        return len(self._returns)


@dataclass
class AverageTrueRange:
    """ATR con smoothing di Wilder.

    A differenza della volatilita' dei rendimenti tiene conto dei gap fra la
    chiusura precedente e il range della barra corrente: e' la misura giusta
    per piazzare uno stop, perche' uno stop viene colpito dal range, non dalla
    deviazione standard delle chiusure.
    """

    window: int
    _atr: float | None = field(default=None, repr=False)
    _previous_close: Decimal | None = field(default=None, repr=False)
    _seed: list[float] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.window < 2:
            raise ValueError(f"finestra ATR {self.window} troppo corta")

    def update(self, candle: Candle) -> None:
        true_range = self._true_range(candle)
        self._previous_close = candle.close

        if self._atr is None:
            self._seed.append(true_range)
            if len(self._seed) == self.window:
                self._atr = sum(self._seed) / self.window
            return
        # Wilder: media mobile esponenziale con alpha = 1 / window
        self._atr = (self._atr * (self.window - 1) + true_range) / self.window

    def _true_range(self, candle: Candle) -> float:
        high_low = float(candle.high - candle.low)
        if self._previous_close is None:
            return high_low
        previous = float(self._previous_close)
        return max(
            high_low,
            abs(float(candle.high) - previous),
            abs(float(candle.low) - previous),
        )

    @property
    def value(self) -> float | None:
        return self._atr

    def value_bps(self, reference_price: Decimal) -> float | None:
        """ATR in punti base del prezzo: confrontabile fra simboli diversi."""
        if self._atr is None or reference_price <= 0:
            return None
        return self._atr / float(reference_price) * 10_000.0
