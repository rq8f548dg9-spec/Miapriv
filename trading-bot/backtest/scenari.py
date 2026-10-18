"""Scenari probabilistici a partire dalle operazioni osservate.

Un backtest racconta una storia sola: quella capitata. La domanda utile
prima di affidare dei soldi a una strategia e' un'altra, e riguarda tutte
le storie che potevano capitare. In cento periodi come quello misurato,
quante volte si finisce sotto? Quanto spesso si tocca il limite di
perdita giornaliero, facendo fermare il bot?

Il metodo e' il ricampionamento a blocchi (*stationary block
bootstrap*): si pescano sequenze consecutive di operazioni gia'
osservate, invece di operazioni singole. La differenza non e'
accademica. Le perdite in fila sono esattamente cio' che fa scattare le
protezioni, e pescare un'operazione alla volta le spezza: ne uscirebbero
scenari sistematicamente piu' rosei del vero.

Due cose che questo modulo non fa, di proposito:

* **non produce percentili su un campione piccolo.** Un intervallo di
  confidenza su otto operazioni e' un numero che somiglia a una misura
  senza esserlo, ed e' peggio di una riga vuota;
* **non prevede il futuro.** Ricampiona il passato osservato. Se il
  mercato cambia regime, questi scenari descrivono un mondo che non c'e'
  piu'. E' un limite del metodo, non un dettaglio da nascondere.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from core.portfolio import TradeResult

__all__ = [
    "MIN_OPERAZIONI_PER_SCENARI",
    "SimulazioneScenari",
    "simula_scenari",
]

#: Sotto questo numero di operazioni non si producono percentili. Non e'
#: una soglia magica: e' il punto sotto il quale la dispersione delle
#: stime e' cosi' larga che qualunque numero si dichiari e' compatibile
#: con qualunque altro.
MIN_OPERAZIONI_PER_SCENARI = 20

_PERCENTILI = (("p05", 5), ("p25", 25), ("p50", 50), ("p75", 75), ("p95", 95))


def _percentile(valori: Sequence[float], quantile: int) -> float:
    """Percentile per interpolazione lineare, senza dipendenze esterne."""
    if not valori:
        return 0.0
    ordinati = sorted(valori)
    if len(ordinati) == 1:
        return ordinati[0]
    posizione = (len(ordinati) - 1) * quantile / 100
    basso = int(posizione)
    alto = min(basso + 1, len(ordinati) - 1)
    peso = posizione - basso
    return ordinati[basso] * (1 - peso) + ordinati[alto] * peso


@dataclass(frozen=True, slots=True)
class SimulazioneScenari:
    """Esito della simulazione. ``affidabile`` falso = non c'e' misura."""

    affidabile: bool
    spiegazione: str
    operazioni_osservate: int
    percorsi_eseguiti: int
    orizzonte: int
    #: Risultato finale in percentuale del capitale, per percentile.
    percentili: dict[str, float] = field(default_factory=dict)
    #: Quota di scenari che finiscono sotto al capitale di partenza.
    probabilita_perdita: float = 0.0
    #: Quota di scenari in cui il calo tocca il limite configurato, cioe'
    #: in cui il bot si sarebbe fermato da solo. ``None`` se non e' stato
    #: dato un limite da verificare.
    probabilita_stop: float | None = None
    #: Distribuzione del calo massimo lungo il percorso, in percentuale.
    calo_massimo: dict[str, float] = field(default_factory=dict)
    #: Risultato medio per operazione nel campione, in valuta di conto.
    media_per_operazione: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "affidabile": self.affidabile,
            "spiegazione": self.spiegazione,
            "operazioni_osservate": self.operazioni_osservate,
            "percorsi": self.percorsi_eseguiti,
            "orizzonte": self.orizzonte,
            "percentili": {k: round(v, 4) for k, v in self.percentili.items()},
            "probabilita_perdita": round(self.probabilita_perdita, 4),
            "probabilita_stop": (
                None if self.probabilita_stop is None else round(self.probabilita_stop, 4)
            ),
            "calo_massimo": {k: round(v, 4) for k, v in self.calo_massimo.items()},
            "media_per_operazione": round(self.media_per_operazione, 6),
        }


def simula_scenari(
    operazioni: Sequence[TradeResult],
    *,
    capitale: Decimal,
    percorsi: int = 2_000,
    orizzonte: int | None = None,
    blocco: int = 5,
    seed: int = 20_261_018,
    limite_perdita: float | None = None,
) -> SimulazioneScenari:
    """Ricampiona le operazioni osservate e descrive gli esiti possibili.

    ``orizzonte`` e' quante operazioni dura ogni scenario: per difetto,
    quante ne sono state osservate. ``limite_perdita`` e' la soglia di
    calo oltre la quale il bot si fermerebbe (la stessa del kill switch),
    e serve a stimare quanto spesso succederebbe.

    Il seme e' fissato: uno scenario che cambia a ogni esecuzione non si
    puo' discutere con nessuno.
    """
    if capitale <= 0:
        raise ValueError(f"capitale {capitale} non positivo")
    if blocco < 1:
        raise ValueError(f"blocco {blocco} non positivo")

    osservate = len(operazioni)
    if osservate < MIN_OPERAZIONI_PER_SCENARI:
        return SimulazioneScenari(
            affidabile=False,
            spiegazione=(
                f"solo {osservate} operazioni osservate: ne servono almeno "
                f"{MIN_OPERAZIONI_PER_SCENARI} perche' uno scenario dica qualcosa. "
                "Con meno, qualunque percentile sarebbe compatibile con qualunque "
                "altro."
            ),
            operazioni_osservate=osservate,
            percorsi_eseguiti=0,
            orizzonte=orizzonte or 0,
        )
    if blocco > osservate:
        raise ValueError(
            f"blocco {blocco} piu' lungo delle {osservate} operazioni osservate"
        )

    risultati = [float(operazione.realized_pnl) for operazione in operazioni]
    passi = orizzonte or osservate
    base = float(capitale)
    # Il generatore standard basta: qui si ricampionano operazioni, non
    # si generano segreti, e il seme fisso e' un requisito.
    rng = random.Random(seed)  # noqa: S311

    finali: list[float] = []
    cali: list[float] = []
    fermati = 0

    for _ in range(percorsi):
        equity = base
        picco = base
        calo_peggiore = 0.0
        si_e_fermato = False
        fatti = 0
        while fatti < passi:
            # Un blocco intero di operazioni consecutive: le serie di
            # perdite restano serie, ed e' quello che fa scattare le
            # protezioni.
            inizio = rng.randrange(osservate)
            for scostamento in range(min(blocco, passi - fatti)):
                equity += risultati[(inizio + scostamento) % osservate]
                fatti += 1
                picco = max(picco, equity)
                calo = (picco - equity) / picco if picco > 0 else 0.0
                calo_peggiore = max(calo_peggiore, calo)
                if limite_perdita is not None and calo >= limite_perdita:
                    si_e_fermato = True
        finali.append((equity - base) / base)
        cali.append(calo_peggiore)
        fermati += int(si_e_fermato)

    return SimulazioneScenari(
        affidabile=True,
        spiegazione=(
            f"{percorsi} scenari da {passi} operazioni, ricampionando a blocchi di "
            f"{blocco} le {osservate} operazioni osservate. Descrive cio' che poteva "
            "succedere in quel mercato: se il regime cambia, non vale piu'."
        ),
        operazioni_osservate=osservate,
        percorsi_eseguiti=percorsi,
        orizzonte=passi,
        percentili={nome: _percentile(finali, q) for nome, q in _PERCENTILI},
        probabilita_perdita=sum(1 for x in finali if x < 0) / len(finali),
        probabilita_stop=(None if limite_perdita is None else fermati / percorsi),
        calo_massimo={nome: _percentile(cali, q) for nome, q in _PERCENTILI},
        media_per_operazione=statistics.fmean(risultati),
    )
