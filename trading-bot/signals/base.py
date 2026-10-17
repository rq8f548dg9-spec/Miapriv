"""Interfaccia dei generatori di segnale.

Un generatore riceve un vettore di feature e restituisce, al massimo, una
proposta: direzione, probabilita' calibrata e piano operativo (dove sta lo
stop, dove sta il target). Non decide la size e non costruisce ordini: quello
e' compito esclusivo del risk manager.

La probabilita' deve essere CALIBRATA, non un punteggio normalizzato alla
bell'e meglio: il risk manager la usa dentro Kelly, e Kelly con probabilita'
gonfiate dimensiona posizioni rovinose.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from core.types import Signal, SignalDirection, TimestampMs
from features.engine import FeatureVector

__all__ = ["GeneratedSignal", "SignalGenerator", "TradePlan", "logistic"]


def logistic(value: float) -> float:
    """Sigmoide numericamente stabile, per mappare un punteggio in (0, 1)."""
    if value >= 0:
        return 1.0 / (1.0 + _exp_neg(value))
    exp_value = _exp_neg(-value)
    return exp_value / (1.0 + exp_value)


def _exp_neg(value: float) -> float:
    import math

    # clamp: oltre questa soglia exp satura comunque e rischia overflow
    return math.exp(-min(value, 700.0))


@dataclass(frozen=True, slots=True)
class TradePlan:
    """Dove mettere stop e target, in punti base del prezzo di ingresso.

    Da qui discendono il ``payoff_ratio`` che Kelly usa per dimensionare e
    l'``expected_move_bps`` con cui il risk manager confronta l'edge con i
    costi. Un piano senza stop non e' un piano.
    """

    stop_bps: float
    take_profit_bps: float

    def __post_init__(self) -> None:
        if self.stop_bps <= 0:
            raise ValueError(f"stop {self.stop_bps} bps non positivo")
        if self.take_profit_bps <= 0:
            raise ValueError(f"take profit {self.take_profit_bps} bps non positivo")

    @property
    def payoff_ratio(self) -> float:
        return self.take_profit_bps / self.stop_bps

    @property
    def expected_move_bps(self) -> float:
        return self.take_profit_bps


@dataclass(frozen=True, slots=True)
class GeneratedSignal:
    """Segnale piu' piano operativo."""

    signal: Signal
    plan: TradePlan

    @property
    def symbol(self) -> str:
        return self.signal.symbol

    @property
    def direction(self) -> SignalDirection:
        return self.signal.direction


class SignalGenerator(ABC):
    """Contratto comune a baseline a regole e modelli.

    Implementazioni intercambiabili: il resto del sistema non deve sapere se
    dietro c'e' una regola di tre righe o un modello addestrato.
    """

    name: str = "base"

    @abstractmethod
    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        """Proposta per questo simbolo, o ``None`` se non c'e' niente da fare.

        Args:
            vector: feature osservabili all'istante della decisione.
            has_position: se esiste gia' una posizione aperta sul simbolo.
                Serve a distinguere un ingresso da un'uscita, non a guardare
                il prezzo di carico: il segnale non deve sapere se siamo in
                profitto, altrimenti impara a tenere i perdenti.
            now_ms: istante della decisione.
        """

    def warmup_satisfied(self, vector: FeatureVector) -> bool:
        return vector.is_complete

    @staticmethod
    def _build_signal(
        *,
        symbol: str,
        now_ms: TimestampMs,
        direction: SignalDirection,
        probability: float,
        features: Mapping[str, float],
        generator: str,
        horizon_ms: int,
        metadata: Mapping[str, object] | None = None,
    ) -> Signal:
        return Signal(
            symbol=symbol,
            ts_ms=now_ms,
            direction=direction,
            probability=probability,
            features=dict(features),
            generator=generator,
            horizon_ms=horizon_ms,
            metadata=dict(metadata or {}),
        )
