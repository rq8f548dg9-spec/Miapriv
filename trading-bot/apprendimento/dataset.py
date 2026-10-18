"""Da candele e trade a campioni etichettati, senza guardare il futuro.

Un campione e' una fotografia di cio' che si sapeva a un certo istante,
piu' una risposta che si e' saputa solo dopo. Le due cose vanno tenute
separate con pedanteria: se una feature calcolata all'istante t contiene
anche solo di striscio il prezzo di t+1, il modello che ne esce sembrera'
bravissimo in prova e inutile dal vivo. E' il modo piu' comune di
ingannarsi con il machine learning sui mercati.

**L'etichetta tiene conto dei costi.** La domanda non e' "il prezzo
sale?" ma "sale abbastanza da pagare commissioni e spread?". Un rialzo di
dieci punti base con ventidue di costi e' un'operazione in perdita, e un
modello addestrato a riconoscerlo impara a perdere con precisione.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from backtest.costs import CostModel
from config.schema import AppConfig, CostsConfig
from core.types import Candle, TimestampMs, Trade
from features.engine import FeatureEngine

__all__ = ["Campione", "costruisci_dataset", "feature_utilizzabili"]

#: Chiavi che non sono informazione di mercato ma contabilita' interna:
#: darle in pasto al modello significa fargli imparare quanto e' vecchia
#: una candela invece di come si muove il prezzo.
_NON_FEATURE = frozenset({"bars_seen", "candle_age_ms", "close", "book_age_ms"})


@dataclass(frozen=True, slots=True)
class Campione:
    """Cosa si sapeva a ``ts_ms``, e com'e' andata dopo."""

    symbol: str
    ts_ms: TimestampMs
    valori: dict[str, float]
    #: 1 se andare long a questo istante avrebbe pagato i costi.
    etichetta: int
    #: Rendimento effettivo sull'orizzonte, in punti base. Serve a
    #: ispezionare, non all'addestramento.
    rendimento_bps: float = 0.0
    metadati: dict[str, str] = field(default_factory=dict)


def feature_utilizzabili(campioni: Sequence[Campione]) -> list[str]:
    """Feature presenti in TUTTI i campioni, in ordine stabile.

    Una feature presente a tratti e' peggio di una assente: il modello la
    userebbe solo nei periodi in cui c'e', cioe' imparerebbe anche quando
    il dato mancava.
    """
    if not campioni:
        return []
    comuni: set[str] = set(campioni[0].valori)
    for campione in campioni[1:]:
        comuni &= set(campione.valori)
    return sorted(comuni - _NON_FEATURE)


def costruisci_dataset(
    config: AppConfig,
    candles: Sequence[Candle],
    trades: Sequence[Trade] = (),
    *,
    orizzonte_barre: int = 5,
    costo_bps: float | None = None,
) -> list[Campione]:
    """Costruisce i campioni ripercorrendo la storia in ordine.

    Le feature all'istante della chiusura di barra escono dallo stesso
    motore che gira dal vivo: usarne uno diverso qui vorrebbe dire
    addestrare un modello su un mondo che poi non incontrera'.

    Le ultime ``orizzonte_barre`` barre non producono campioni: il loro
    futuro non e' ancora successo, e inventarlo e' esattamente cio' che
    questo modulo esiste per evitare.
    """
    if orizzonte_barre < 1:
        raise ValueError(f"orizzonte {orizzonte_barre} non positivo")

    costi = CostModel(config.backtest.costs if config.backtest else CostsConfig())
    soglia_bps = (
        costo_bps
        if costo_bps is not None
        else costi.round_trip_bps(config.risk.sizing.target_order_quote)
    )

    ordinate = sorted(candles, key=lambda c: (c.close_time_ms, c.symbol))
    per_simbolo: dict[str, list[Candle]] = {}
    for candela in ordinate:
        if candela.is_closed:
            per_simbolo.setdefault(candela.symbol, []).append(candela)

    motore = FeatureEngine(
        config.features, trade_capacity=config.data.buffer_capacity
    )
    flusso = sorted(trades, key=lambda t: (t.ts_ms, t.trade_id))
    prossimo = 0
    indici: dict[str, int] = dict.fromkeys(per_simbolo, 0)
    campioni: list[Campione] = []

    for candela in ordinate:
        if not candela.is_closed:
            continue
        # I trade fino a questo istante, e non uno di piu'.
        while prossimo < len(flusso) and flusso[prossimo].ts_ms <= candela.close_time_ms:
            motore.on_trade(flusso[prossimo])
            prossimo += 1
        motore.on_candle(candela)

        serie = per_simbolo[candela.symbol]
        posizione = indici[candela.symbol]
        indici[candela.symbol] = posizione + 1

        futuro = posizione + orizzonte_barre
        if futuro >= len(serie):
            continue  # il futuro di questa barra non e' ancora successo

        vettore = motore.compute(candela.symbol, candela.close_time_ms)
        if not vettore.is_complete:
            continue

        prezzo = candela.close
        dopo = serie[futuro].close
        if prezzo <= 0:
            continue
        rendimento_bps = float((dopo - prezzo) / prezzo) * 10_000.0
        campioni.append(
            Campione(
                symbol=candela.symbol,
                ts_ms=candela.close_time_ms,
                valori={
                    nome: valore
                    for nome, valore in vettore.values.items()
                    if nome not in _NON_FEATURE
                },
                etichetta=int(rendimento_bps > soglia_bps),
                rendimento_bps=rendimento_bps,
                metadati={"prezzo": str(prezzo), "prezzo_dopo": str(dopo)},
            )
        )
    return campioni


def quota_positivi(campioni: Sequence[Campione]) -> float:
    """Frazione di campioni etichettati 1.

    Serve a riconoscere subito il caso in cui i costi rendono l'etichetta
    quasi sempre zero: li' non c'e' niente da imparare, e insistere
    produce un modello che dice sempre no.
    """
    if not campioni:
        return 0.0
    return sum(c.etichetta for c in campioni) / len(campioni)


def _decimale(valore: float) -> Decimal:
    return Decimal(str(valore))
