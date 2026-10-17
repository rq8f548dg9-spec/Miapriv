"""Baseline a regole: la prima cosa da battere.

Non e' un modello e non pretende di esserlo. E' una combinazione lineare di
tre effetti documentati in letteratura sulla microstruttura:

* order flow imbalance: chi consuma liquidita' in modo sbilanciato muove il
  prezzo nella direzione dello sbilanciamento, su orizzonti di secondi;
* book imbalance: un book sbilanciato precede spesso un movimento del mid;
* mean reversion di breve: un movimento anomalo rispetto alla volatilita'
  recente tende a rientrare parzialmente.

I primi due spingono nella direzione del flusso, il terzo contro. Il peso
relativo sta in configurazione, non nel codice.

Il punto di questa baseline non e' guadagnare: e' fornire un metro. Se un
modello non la batte al netto dei costi, il modello non serve.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.types import SignalDirection, TimestampMs
from features.engine import FeatureVector
from signals.base import GeneratedSignal, SignalGenerator, TradePlan, logistic

__all__ = ["RuleBasedSignalGenerator", "RuleWeights"]


@dataclass(frozen=True, slots=True)
class RuleWeights:
    """Pesi della combinazione lineare, prima della sigmoide.

    ``scale`` converte il punteggio in logit: piu' e' alto, piu' il generatore
    e' sicuro di se'. Tenuto basso di proposito: una baseline che produce
    probabilita' estreme fa dimensionare a Kelly posizioni che non merita.
    """

    order_flow: float = 1.0
    book_imbalance: float = 0.6
    microprice: float = 0.4
    mean_reversion: float = -0.3
    scale: float = 0.8
    #: Ampiezza dello stop in multipli di ATR.
    stop_atr_multiple: float = 1.0
    #: Rapporto fra target e stop.
    payoff_ratio: float = 1.5
    #: Stop minimo, per non piazzarlo dentro lo spread.
    min_stop_bps: float = 5.0

    def __post_init__(self) -> None:
        if self.scale <= 0:
            raise ValueError("scale deve essere positivo")
        if self.payoff_ratio <= 0:
            raise ValueError("payoff_ratio deve essere positivo")
        if self.stop_atr_multiple <= 0:
            raise ValueError("stop_atr_multiple deve essere positivo")


@dataclass
class RuleBasedSignalGenerator(SignalGenerator):
    """Generatore a regole, deterministico e ispezionabile."""

    weights: RuleWeights = RuleWeights()
    horizon_ms: int = 300_000
    #: Soglia del punteggio sotto la quale una posizione aperta viene chiusa.
    exit_score_threshold: float = 0.0
    name: str = "rules"

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        if not self.warmup_satisfied(vector):
            return None

        score = self.score(vector)
        plan = self._plan(vector)
        if plan is None:
            return None

        if has_position and score <= self.exit_score_threshold:
            # Uscita: la vista che aveva motivato l'ingresso non c'e' piu'.
            return GeneratedSignal(
                signal=self._build_signal(
                    symbol=vector.symbol,
                    now_ms=now_ms,
                    direction=SignalDirection.FLAT,
                    probability=logistic(-score * self.weights.scale),
                    features=vector.values,
                    generator=self.name,
                    horizon_ms=self.horizon_ms,
                    metadata={"score": score, "motivo": "punteggio sotto la soglia di uscita"},
                ),
                plan=plan,
            )

        if score <= 0:
            # Nessuna vista rialzista e nessuna posizione da chiudere: si sta fermi.
            return None

        probability = logistic(score * self.weights.scale)
        return GeneratedSignal(
            signal=self._build_signal(
                symbol=vector.symbol,
                now_ms=now_ms,
                direction=SignalDirection.LONG,
                probability=probability,
                features=vector.values,
                generator=self.name,
                horizon_ms=self.horizon_ms,
                metadata={"score": score, "stop_bps": plan.stop_bps},
            ),
            plan=plan,
        )

    def score(self, vector: FeatureVector) -> float:
        """Punteggio grezzo, positivo = rialzista. Ogni addendo e' ispezionabile."""
        weights = self.weights
        # L'OFI piu' reattivo disponibile fra quelli calcolati.
        flow = self._first_available(vector, ("ofi_1s", "ofi_5s", "ofi_30s"))
        book = vector.get("book_imbalance", 0.0)
        microprice = _clip(vector.get("book_microprice_dev_bps", 0.0) / 10.0, -1.0, 1.0)
        reversion = _clip(vector.get("ret_zscore", 0.0) / 3.0, -1.0, 1.0)

        return (
            weights.order_flow * flow
            + weights.book_imbalance * book
            + weights.microprice * microprice
            + weights.mean_reversion * reversion
        )

    def contributions(self, vector: FeatureVector) -> dict[str, float]:
        """Scomposizione del punteggio, per l'autopsia di una decisione."""
        weights = self.weights
        return {
            "order_flow": weights.order_flow
            * self._first_available(vector, ("ofi_1s", "ofi_5s", "ofi_30s")),
            "book_imbalance": weights.book_imbalance * vector.get("book_imbalance", 0.0),
            "microprice": weights.microprice
            * _clip(vector.get("book_microprice_dev_bps", 0.0) / 10.0, -1.0, 1.0),
            "mean_reversion": weights.mean_reversion
            * _clip(vector.get("ret_zscore", 0.0) / 3.0, -1.0, 1.0),
        }

    def _plan(self, vector: FeatureVector) -> TradePlan | None:
        """Stop proporzionale all'ATR: in mercati volatili lo stop si allarga.

        Senza ATR non c'e' piano, e senza piano non c'e' segnale: meglio
        fermarsi che inventare una distanza di stop.
        """
        atr_bps = vector.values.get("atr_bps")
        if atr_bps is None or atr_bps <= 0:
            return None
        stop_bps = max(atr_bps * self.weights.stop_atr_multiple, self.weights.min_stop_bps)
        return TradePlan(
            stop_bps=stop_bps, take_profit_bps=stop_bps * self.weights.payoff_ratio
        )

    @staticmethod
    def _first_available(vector: FeatureVector, names: tuple[str, ...]) -> float:
        for name in names:
            if name in vector.values:
                return vector.values[name]
        return 0.0


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
