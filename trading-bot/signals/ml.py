"""Segmaposto per il modello predittivo. NON e' implementato di proposito.

Il modello si scrive solo se la baseline a regole regge i costi di
transazione nel backtest. Finche' quel risultato non esiste, un modello
aggiunge soltanto gradi di liberta' con cui illudersi.

Quello che c'e' qui e' il contratto che un modello dovra' rispettare, perche'
sia intercambiabile con la baseline e perche' il risk manager possa fidarsi
del numero che gli arriva:

* output = probabilita' CALIBRATA, non una classe secca ne' un punteggio
  normalizzato. "Calibrata" ha un significato verificabile: fra gli eventi a
  cui il modello assegna 0,60 deve verificarsene circa il 60%.
* ``reliability_curve`` qui sotto misura esattamente questo, e va eseguita
  su dati fuori campione prima di collegare un modello al denaro.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from core.types import TimestampMs
from features.engine import FeatureVector
from signals.base import GeneratedSignal, SignalGenerator

__all__ = [
    "CalibratedModel",
    "MLSignalGenerator",
    "ModelNotImplementedError",
    "expected_calibration_error",
    "reliability_curve",
]


class ModelNotImplementedError(NotImplementedError):
    """Sollevata finche' il modello resta un segnaposto."""


class CalibratedModel(Protocol):
    """Contratto minimo di un modello collegabile al bot."""

    #: Nomi delle feature attese, nell'ordine. Serve a bloccare un vettore
    #: incompleto o riordinato invece di predire su dati sbagliati.
    feature_names: Sequence[str]

    def predict_proba(self, features: Sequence[float]) -> float:
        """Probabilita' calibrata in [0, 1] che la direzione long sia corretta."""
        ...


def reliability_curve(
    probabilities: Sequence[float], outcomes: Sequence[bool], bins: int = 10
) -> list[tuple[float, float, int]]:
    """Curva di affidabilita': (probabilita' media prevista, frequenza reale, conteggio).

    Un modello calibrato produce punti vicini alla bisettrice. Uno
    sovraconfidente produce frequenze reali sistematicamente sotto le
    probabilita' previste, ed e' il modo piu' rapido di farsi male con Kelly.
    """
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilita' e esiti devono avere la stessa lunghezza")
    if bins < 2:
        raise ValueError(f"servono almeno 2 bin, ricevuti {bins}")

    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for probability, outcome in zip(probabilities, outcomes, strict=True):
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"probabilita' {probability} fuori da [0, 1]")
        index = min(int(probability * bins), bins - 1)
        buckets[index].append((probability, outcome))

    curve: list[tuple[float, float, int]] = []
    for bucket in buckets:
        if not bucket:
            continue
        predicted = sum(p for p, _ in bucket) / len(bucket)
        observed = sum(1 for _, o in bucket if o) / len(bucket)
        curve.append((predicted, observed, len(bucket)))
    return curve


def expected_calibration_error(
    probabilities: Sequence[float], outcomes: Sequence[bool], bins: int = 10
) -> float:
    """Errore di calibrazione atteso: scarto medio pesato dalla bisettrice.

    Soglia di buon senso: sopra 0,05 il modello non e' utilizzabile dentro
    Kelly, perche' sbaglia la probabilita' di piu' di quanto valga l'edge.
    """
    curve = reliability_curve(probabilities, outcomes, bins)
    total = sum(count for _, _, count in curve)
    if total == 0:
        return 0.0
    return sum(abs(predicted - observed) * count for predicted, observed, count in curve) / total


@dataclass
class MLSignalGenerator(SignalGenerator):
    """Adattatore fra un modello calibrato e il resto del sistema.

    Oggi rifiuta di generare segnali. Quando ci sara' un modello, l'unica
    cosa da implementare e' il corpo di ``generate``: la firma, i controlli
    sulle feature e il contratto sulla probabilita' sono gia' fissati.
    """

    model: CalibratedModel | None = None
    model_path: Path | None = None
    horizon_ms: int = 300_000
    #: Errore di calibrazione misurato fuori campione. Senza questo numero il
    #: modello non va collegato: Kelly si fida della probabilita'.
    measured_calibration_error: float | None = None
    max_calibration_error: float = 0.05
    name: str = "ml"

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        raise ModelNotImplementedError(
            "signals/ml.py e' un segnaposto. Il modello si implementa solo dopo che "
            "la baseline a regole ha retto i costi di transazione nel backtest "
            "(fase 6). Fino ad allora usare generator='rules'."
        )

    def validate_model(self) -> None:
        """Controlli da superare prima di collegare un modello al denaro."""
        if self.model is None:
            raise ModelNotImplementedError("nessun modello caricato")
        if self.measured_calibration_error is None:
            raise ValueError(
                "calibrazione non misurata: eseguire reliability_curve su dati "
                "fuori campione prima di usare il modello"
            )
        if self.measured_calibration_error > self.max_calibration_error:
            raise ValueError(
                f"errore di calibrazione {self.measured_calibration_error:.4f} oltre "
                f"il massimo {self.max_calibration_error:.4f}: le probabilita' non "
                "sono affidabili dentro Kelly"
            )

    def extract(self, vector: FeatureVector) -> list[float]:
        """Feature nell'ordine atteso dal modello.

        Una feature mancante e' un errore, non uno zero: predire su un vettore
        incompleto produce una probabilita' sbagliata che sembra valida.
        """
        if self.model is None:
            raise ModelNotImplementedError("nessun modello caricato")
        missing = [name for name in self.model.feature_names if name not in vector.values]
        if missing:
            raise ValueError(f"feature mancanti nel vettore: {missing}")
        return [vector.values[name] for name in self.model.feature_names]
