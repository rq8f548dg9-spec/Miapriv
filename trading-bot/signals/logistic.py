"""Regressione logistica scritta a mano, senza dipendenze nuove.

Perche' a mano: aggiungere una libreria di machine learning a un bot che
muove denaro significa aggiungere codice che nessuno qui ha letto, e per
un modello lineare su una manciata di feature non serve. Quello che serve
e' che il numero che esce sia una probabilita' vera, e quello dipende
dalla calibrazione, non dalla libreria.

Due scelte che contano piu' dell'algoritmo:

* **le feature si normalizzano con media e scarto del campione di
  addestramento**, salvati nel modello. Normalizzare con le statistiche
  del momento in cui si predice vorrebbe dire usare dati che al momento
  della decisione non si avevano;
* **la regolarizzazione e' sempre attiva.** Su poche centinaia di
  campioni e una decina di feature, un modello senza freno impara il
  rumore e dichiara convinzioni che non ha.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from apprendimento.dataset import Campione

__all__ = ["ModelloLogistico", "addestra"]


def _sigmoide(z: float) -> float:
    # Forma stabile: exp su numeri grandi trabocca, e un modello che
    # esplode su un valore estremo e' un modello che sbaglia proprio dove
    # il mercato si muove.
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    esponenziale = math.exp(z)
    return esponenziale / (1.0 + esponenziale)


@dataclass
class ModelloLogistico:
    """Modello lineare calibrato, con le sue statistiche di normalizzazione."""

    feature_names: list[str]
    pesi: list[float]
    intercetta: float
    medie: list[float]
    scarti: list[float]
    #: Errore di calibrazione misurato fuori campione, se misurato.
    errore_calibrazione: float | None = None
    campioni_addestramento: int = 0
    metadati: dict[str, Any] = field(default_factory=dict)

    def predict_proba(self, features: Sequence[float]) -> float:
        if len(features) != len(self.feature_names):
            raise ValueError(
                f"attese {len(self.feature_names)} feature, ricevute {len(features)}"
            )
        z = self.intercetta
        for valore, peso, media, scarto in zip(
            features, self.pesi, self.medie, self.scarti, strict=True
        ):
            z += peso * ((valore - media) / scarto)
        return _sigmoide(z)

    def salva(
        self, percorso: Path, *, errore_calibrazione: float, campioni: int
    ) -> None:
        self.errore_calibrazione = errore_calibrazione
        self.campioni_addestramento = campioni
        percorso.parent.mkdir(parents=True, exist_ok=True)
        percorso.write_text(
            json.dumps(
                {
                    "feature_names": self.feature_names,
                    "pesi": self.pesi,
                    "intercetta": self.intercetta,
                    "medie": self.medie,
                    "scarti": self.scarti,
                    "errore_calibrazione": errore_calibrazione,
                    "campioni_addestramento": campioni,
                    "metadati": self.metadati,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    @classmethod
    def carica(cls, percorso: Path) -> ModelloLogistico:
        dati = json.loads(Path(percorso).read_text(encoding="utf-8"))
        return cls(
            feature_names=list(dati["feature_names"]),
            pesi=[float(x) for x in dati["pesi"]],
            intercetta=float(dati["intercetta"]),
            medie=[float(x) for x in dati["medie"]],
            scarti=[float(x) for x in dati["scarti"]],
            errore_calibrazione=dati.get("errore_calibrazione"),
            campioni_addestramento=int(dati.get("campioni_addestramento", 0)),
            metadati=dict(dati.get("metadati", {})),
        )


def addestra(
    campioni: Sequence[Campione],
    *,
    feature: Sequence[str],
    passi: int = 500,
    passo: float = 0.3,
    regolarizzazione: float = 0.01,
) -> ModelloLogistico:
    """Discesa del gradiente sulla log-verosimiglianza, con freno L2."""
    if not campioni:
        raise ValueError("nessun campione su cui addestrare")
    nomi = list(feature)
    if not nomi:
        raise ValueError("nessuna feature indicata")

    righe: list[list[float]] = []
    etichette: list[int] = []
    for campione in campioni:
        mancanti = [nome for nome in nomi if nome not in campione.valori]
        if mancanti:
            continue
        righe.append([campione.valori[nome] for nome in nomi])
        etichette.append(campione.etichetta)

    if not righe:
        raise ValueError("nessun campione contiene tutte le feature richieste")
    if len(set(etichette)) < 2:
        raise ValueError(
            "una sola classe nei dati: non c'e' niente da separare, e un modello "
            "addestrato cosi' direbbe sempre la stessa cosa"
        )

    n = len(righe)
    colonne = len(nomi)
    medie = [sum(riga[j] for riga in righe) / n for j in range(colonne)]
    scarti: list[float] = []
    for j in range(colonne):
        varianza = sum((riga[j] - medie[j]) ** 2 for riga in righe) / n
        # Scarto nullo = feature costante: dividere per zero no, e il peso
        # resta a zero da solo perche' il contributo e' sempre lo stesso.
        scarti.append(math.sqrt(varianza) if varianza > 1e-12 else 1.0)

    normalizzate = [
        [(riga[j] - medie[j]) / scarti[j] for j in range(colonne)] for riga in righe
    ]

    pesi = [0.0] * colonne
    intercetta = 0.0
    for _ in range(passi):
        grad_pesi = [0.0] * colonne
        grad_intercetta = 0.0
        for riga, y in zip(normalizzate, etichette, strict=True):
            z = intercetta + sum(p * x for p, x in zip(pesi, riga, strict=True))
            errore = _sigmoide(z) - y
            grad_intercetta += errore
            for j in range(colonne):
                grad_pesi[j] += errore * riga[j]
        intercetta -= passo * grad_intercetta / n
        for j in range(colonne):
            pesi[j] -= passo * (grad_pesi[j] / n + regolarizzazione * pesi[j])

    return ModelloLogistico(
        feature_names=nomi,
        pesi=pesi,
        intercetta=intercetta,
        medie=medie,
        scarti=scarti,
        campioni_addestramento=n,
    )
