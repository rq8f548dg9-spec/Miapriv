"""Rendimenti multi-timeframe e z-score.

Il rendimento grezzo non e' confrontabile fra simboli ne' fra regimi: lo
z-score lo normalizza sulla distribuzione recente dello stesso simbolo. Un
+30 bps su BTC in un'ora calma e un +30 bps durante un crollo sono due cose
diverse, e il modello deve poterle distinguere.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["MomentumFeatures"]


@dataclass
class MomentumFeatures:
    """Rendimenti su piu' orizzonti piu' lo z-score del rendimento a una barra."""

    windows: tuple[int, ...]
    zscore_window: int
    _closes: deque[Decimal] = field(default_factory=deque, repr=False)
    _one_bar_returns: deque[float] = field(default_factory=deque, repr=False)

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("serve almeno una finestra")
        if self.zscore_window < 10:
            raise ValueError(f"finestra z-score {self.zscore_window} troppo corta")
        # serve una barra in piu' della finestra massima per calcolarne il rendimento
        self._closes = deque(maxlen=max(self.windows) + 1)
        self._one_bar_returns = deque(maxlen=self.zscore_window)

    def update(self, close: Decimal) -> None:
        if close <= 0:
            raise ValueError(f"prezzo di chiusura {close} non positivo")
        if self._closes:
            previous = self._closes[-1]
            self._one_bar_returns.append(float(close / previous) - 1.0)
        self._closes.append(close)

    def ret(self, window: int) -> float | None:
        """Rendimento semplice su ``window`` barre, o ``None`` se mancano dati."""
        if len(self._closes) < window + 1:
            return None
        return float(self._closes[-1] / self._closes[-1 - window]) - 1.0

    def zscore(self) -> float | None:
        """Z-score dell'ultimo rendimento rispetto alla distribuzione recente."""
        if len(self._one_bar_returns) < self.zscore_window:
            return None
        sample = list(self._one_bar_returns)
        mean = sum(sample) / len(sample)
        variance = sum((value - mean) ** 2 for value in sample) / (len(sample) - 1)
        if variance <= 0:
            # prezzo fermo: lo z-score non e' definito, non e' zero
            return None
        return (sample[-1] - mean) / math.sqrt(variance)

    def features(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for window in self.windows:
            value = self.ret(window)
            if value is not None:
                result[f"ret_{window}"] = value
                result[f"ret_{window}_bps"] = value * 10_000.0
        zscore = self.zscore()
        if zscore is not None:
            result["ret_zscore"] = zscore
        return result

    @property
    def samples(self) -> int:
        return len(self._closes)
