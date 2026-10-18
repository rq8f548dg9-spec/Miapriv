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

from core.types import SignalDirection, TimestampMs
from features.engine import FeatureVector
from signals.base import GeneratedSignal, SignalGenerator, TradePlan

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

    @property
    def feature_names(self) -> Sequence[str]:
        """Nomi delle feature attese, nell'ordine.

        Serve a bloccare un vettore incompleto o riordinato invece di
        predire su dati sbagliati. E' di sola lettura nel contratto: chi
        implementa puo' tenerli in una lista.
        """
        ...

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
    #: Stop e target come multipli dell'ATR, gli stessi della baseline:
    #: il modello stima la probabilita', non la volatilita'.
    stop_atr_multiple: float = 1.0
    target_atr_multiple: float = 2.0
    name: str = "ml"

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        """Probabilita' dal modello, piano dallo stesso ATR della baseline.

        Il modello dice quanto e' probabile che andare long adesso paghi i
        costi. Non dice dove mettere lo stop: quello resta ancorato alla
        volatilita' misurata, come per le regole. Senza ATR non c'e' stop,
        e senza stop non si apre niente.
        """
        if self.model is None:
            raise ModelNotImplementedError(
                "nessun modello caricato: addestralo con scripts/addestra.py, "
                "oppure usa generator='rules'."
            )
        if not self.warmup_satisfied(vector):
            return None

        atr_bps = vector.values.get("atr_bps")
        if atr_bps is None or atr_bps <= 0:
            return None

        mancanti = [n for n in self.model.feature_names if n not in vector.values]
        if mancanti:
            # Una feature assente non vale zero: predire su un vettore
            # incompleto produce una probabilita' sbagliata che sembra
            # valida, ed e' peggio di non predire.
            return None

        probabilita = self.model.predict_proba(self.extract(vector))
        direzione = SignalDirection.LONG if probabilita > 0.5 else SignalDirection.FLAT
        if direzione is SignalDirection.FLAT and not has_position:
            return None

        plan = TradePlan(
            stop_bps=float(atr_bps) * self.stop_atr_multiple,
            take_profit_bps=float(atr_bps) * self.target_atr_multiple,
        )
        return GeneratedSignal(
            signal=self._build_signal(
                symbol=vector.symbol,
                now_ms=now_ms,
                direction=direzione,
                probability=probabilita,
                features=vector.values,
                generator=self.name,
                horizon_ms=self.horizon_ms,
                metadata={
                    "errore_calibrazione": self.measured_calibration_error,
                    "campioni_addestramento": getattr(
                        self.model, "campioni_addestramento", None
                    ),
                },
            ),
            plan=plan,
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
