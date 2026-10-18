"""Esperimenti sui parametri, con una regola di promozione scritta prima.

Il modo normale di "far migliorare" un bot e' provare cento varianti sugli
stessi dati e tenere quella che ha reso di piu'. E' anche il modo normale
di ottenere un bot che ha imparato a memoria il passato: con cento
tentativi la migliore sembra brava anche quando sono tutte inutili, ed e'
una proprieta' dei numeri, non della strategia.

Qui ci sono tre difese, tutte noiose e tutte necessarie.

1. **La regola di promozione e' scritta prima di guardare i risultati**, ed
   e' sulla coerenza fra finestre temporali separate, non sulla media. Una
   variante che vince una finestra su cinque puo' avere una media positiva
   senza essere una strategia.
2. **Ogni esperimento resta scritto**, con l'impronta dei dati su cui e'
   girato. Ripetere la stessa misura sugli stessi dati non aggiunge
   informazione, ma ripetuta abbastanza volte prima o poi "vince".
3. **Niente viene applicato da solo.** Questo modulo propone e motiva; la
   configurazione la cambia una persona. Un bot che si riscrive i
   parametri mentre opera e' un bot di cui nessuno sa piu' cosa stia
   facendo.

I parametri di rischio sono fuori dalla ricerca, per costruzione: stop,
limiti ed esposizione non sono numeri da ottimizzare contro il passato.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel

from backtest.engine import BacktestEngine
from backtest.walkforward import run_walk_forward
from config.schema import AppConfig
from core.types import Candle, TimestampMs
from signals.base import SignalGenerator

__all__ = [
    "CANDIDATI_STANDARD",
    "MIN_COERENZA",
    "MIN_FINESTRE",
    "MIN_OPERAZIONI",
    "Candidato",
    "Decisione",
    "EsitoCandidato",
    "RegistroEsperimenti",
    "decidi_promozione",
    "impronta_dei_dati",
    "valuta_candidati",
]

logger = logging.getLogger("trading_bot.apprendimento")

#: Soglie della regola di promozione. Stanno qui, in chiaro e in un posto
#: solo, perche' una regola che si puo' spostare dopo aver visto i
#: risultati non e' una regola.
MIN_FINESTRE = 3
MIN_COERENZA = 0.6
MIN_OPERAZIONI = 30

#: Prefissi che un esperimento non puo' toccare. Non e' una questione di
#: prudenza: allentare uno stop fa sempre sembrare migliore il passato,
#: perche' toglie le chiusure che avevano fatto male. Cercare parametri
#: li' dentro significa cercare la variante che ha rischiato di piu'.
_PROTETTI = ("risk.", "mode", "exchange.")


@lru_cache(maxsize=1)
def _percorsi_dichiarati() -> frozenset[str]:
    """Percorsi puntati che esistono nello schema di configurazione.

    Si leggono dai campi dichiarati, non da un'istanza: cosi' un nome
    sbagliato viene rifiutato quando si scrive l'esperimento, non mezz'ora
    dopo a meta' della misura.
    """
    percorsi: set[str] = set()

    def visita(prefisso: str, modello: type[BaseModel]) -> None:
        for nome, campo in modello.model_fields.items():
            completo = f"{prefisso}{nome}"
            percorsi.add(completo)
            for annotazione in _modelli_in(campo.annotation):
                visita(f"{completo}.", annotazione)

    visita("", AppConfig)
    return frozenset(percorsi)


def _modelli_in(annotazione: Any) -> list[type[BaseModel]]:
    """I modelli pydantic dentro un'annotazione, anche se opzionale."""
    trovati: list[type[BaseModel]] = []
    candidati = [annotazione, *get_args(annotazione)]
    for candidato in candidati:
        if isinstance(candidato, type) and issubclass(candidato, BaseModel):
            trovati.append(candidato)
    return trovati


@dataclass(frozen=True, slots=True)
class Candidato:
    """Una variante da provare: solo cosa cambia, e perche'."""

    nome: str
    motivo: str
    modifiche: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.modifiche:
            raise ValueError("un candidato senza modifiche non e' un esperimento")
        for percorso in self.modifiche:
            if percorso not in _percorsi_dichiarati():
                raise ValueError(
                    f"parametro sconosciuto: {percorso}. Un esperimento su un "
                    "parametro che non esiste misura il nulla."
                )
            if percorso.startswith(_PROTETTI):
                raise ValueError(
                    f"{percorso} tocca le protezioni: stop, limiti e modalita' non "
                    "sono parametri da cercare contro il passato. Allentarli fa "
                    "sempre sembrare migliore la storia gia' avvenuta."
                )

    def applica(self, config: AppConfig) -> AppConfig:
        """Configurazione con le sole modifiche del candidato."""
        raw = config.model_dump()
        for percorso, valore in self.modifiche.items():
            parti = percorso.split(".")
            nodo: Any = raw
            for pezzo in parti[:-1]:
                nodo = nodo[pezzo]
            nodo[parti[-1]] = valore
        return AppConfig.model_validate(raw)

    @property
    def impronta(self) -> str:
        testo = json.dumps(self.modifiche, sort_keys=True, default=str)
        return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class EsitoCandidato:
    """Come e' andata la variante, finestra per finestra."""

    candidato: Candidato
    finestre_valutate: int
    finestre_in_profitto: int
    coerenza: float | None
    rendimento_medio: float | None
    operazioni: int
    calo_peggiore: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidato": self.candidato.nome,
            "motivo": self.candidato.motivo,
            "modifiche": dict(self.candidato.modifiche),
            "impronta_candidato": self.candidato.impronta,
            "finestre_valutate": self.finestre_valutate,
            "finestre_in_profitto": self.finestre_in_profitto,
            "coerenza": self.coerenza,
            "rendimento_medio": self.rendimento_medio,
            "operazioni": self.operazioni,
            "calo_peggiore": self.calo_peggiore,
        }


@dataclass(frozen=True, slots=True)
class Decisione:
    """Promosso o no, e perche'. Il motivo conta quanto l'esito."""

    promosso: bool
    motivo: str


def decidi_promozione(esito: EsitoCandidato) -> Decisione:
    """Applica la regola scritta sopra. Nessuna eccezione, nessun caso speciale."""
    if esito.finestre_valutate < MIN_FINESTRE:
        return Decisione(
            False,
            f"solo {esito.finestre_valutate} finestre valutate su {MIN_FINESTRE} "
            "necessarie: non c'e' modo di distinguere una strategia da una "
            "coincidenza.",
        )
    if esito.operazioni < MIN_OPERAZIONI:
        return Decisione(
            False,
            f"solo {esito.operazioni} operazioni in totale: sotto {MIN_OPERAZIONI} "
            "il risultato e' rumore, qualunque sia.",
        )
    if esito.coerenza is None or esito.coerenza < MIN_COERENZA:
        coerenza = "n/d" if esito.coerenza is None else f"{esito.coerenza:.0%}"
        return Decisione(
            False,
            f"in profitto in {esito.finestre_in_profitto} finestre su "
            f"{esito.finestre_valutate} ({coerenza}): sotto il {MIN_COERENZA:.0%} "
            "richiesto. Vincere in una finestra e perdere nelle altre puo' dare "
            "una media positiva senza essere una strategia.",
        )
    if esito.rendimento_medio is None or esito.rendimento_medio <= 0:
        return Decisione(
            False, "rendimento medio non positivo sulle finestre di prova."
        )
    return Decisione(
        True,
        f"in profitto in {esito.finestre_in_profitto} finestre su "
        f"{esito.finestre_valutate}, rendimento medio "
        f"{esito.rendimento_medio:+.2%}, calo peggiore {esito.calo_peggiore:.2%} su "
        f"{esito.operazioni} operazioni. Resta una proposta: applicarla e' una "
        "decisione di chi mette i soldi.",
    )


def impronta_dei_dati(candles: Sequence[Candle]) -> str:
    """Identifica il campione: stessi dati, stessa impronta.

    Serve a riconoscere la stessa misura rifatta. Si usano gli estremi e
    il conteggio invece di digerire ogni barra: e' abbastanza per
    distinguere due campioni diversi senza costare un giro su milioni di
    righe.
    """
    if not candles:
        return "vuoto"
    ordinate = sorted(candles, key=lambda c: c.close_time_ms)
    simboli = ",".join(sorted({c.symbol for c in candles}))
    testo = (
        f"{simboli}|{len(ordinate)}|{ordinate[0].open_time_ms}|"
        f"{ordinate[-1].close_time_ms}|{ordinate[-1].close}"
    )
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:16]


def valuta_candidati(
    config: AppConfig,
    candles: Sequence[Candle],
    candidati: Sequence[Candidato],
    *,
    generatore: Callable[[AppConfig], SignalGenerator] | None = None,
    bar_duration_ms: int = 60_000,
) -> list[EsitoCandidato]:
    """Misura ogni variante in walk-forward, su finestre separate.

    Le finestre di addestramento restano separate da quelle di prova, con
    purge ed embargo gia' applicati dal modulo walkforward: misurare su
    dati che hanno partecipato alla scelta e' il modo piu' comune di
    ottenere un risultato che non si ripresenta mai.
    """
    if generatore is None:
        from runner import build_generator as _build

        generatore = _build

    esiti: list[EsitoCandidato] = []
    for candidato in candidati:
        cfg = candidato.applica(config)
        motore = generatore(cfg)

        def fabbrica(
            configurazione: AppConfig,
            _train: Sequence[Candle],
            motore: SignalGenerator = motore,
        ) -> BacktestEngine:
            return BacktestEngine(configurazione, motore)

        report = run_walk_forward(
            cfg, candles, fabbrica, bar_duration_ms=bar_duration_ms
        )
        esiti.append(
            EsitoCandidato(
                candidato=candidato,
                finestre_valutate=len(report.evaluated),
                finestre_in_profitto=report.profitable_windows,
                coerenza=report.consistency,
                rendimento_medio=report.mean_return,
                operazioni=report.total_trades,
                calo_peggiore=report.worst_drawdown,
            )
        )
    return esiti


@dataclass
class RegistroEsperimenti:
    """Storico degli esperimenti, in sola aggiunta.

    Senza, si rifa' la stessa prova finche' non viene bene e nessuno se
    ne accorge. Con, la domanda "quante volte abbiamo gia' guardato
    questi dati?" ha una risposta.
    """

    path: Path
    _righe: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.path.exists():
            for riga in self.path.read_text(encoding="utf-8").splitlines():
                if riga.strip():
                    self._righe.append(json.loads(riga))

    def storia(self) -> list[dict[str, Any]]:
        return list(self._righe)

    def gia_provato(self, candidato: Candidato, impronta_dati: str) -> bool:
        return any(
            riga.get("impronta_candidato") == candidato.impronta
            and riga.get("impronta_dati") == impronta_dati
            for riga in self._righe
        )

    def annota(
        self, esito: EsitoCandidato, *, impronta_dati: str, ts_ms: TimestampMs
    ) -> Decisione:
        """Scrive l'esperimento e la decisione che ne consegue."""
        decisione = decidi_promozione(esito)
        riga = {
            **esito.to_dict(),
            "impronta_dati": impronta_dati,
            "ts_ms": ts_ms,
            "promosso": decisione.promosso,
            "motivo": decisione.motivo,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(riga, ensure_ascii=False, sort_keys=True) + "\n")
        self._righe.append(riga)
        logger.info(
            "esperimento %s: %s (%s)",
            esito.candidato.nome,
            "proposto" if decisione.promosso else "scartato",
            decisione.motivo,
        )
        return decisione


#: Le varianti che il pannello prova. Sono poche e scelte prima: una
#: ricerca su cento combinazioni trova sempre qualcosa che ha funzionato
#: sul passato, ed e' il modo piu' rapido di costruire un bot che sa solo
#: ripetere a memoria.
CANDIDATI_STANDARD: tuple[Candidato, ...] = (
    Candidato(
        nome="soglia di probabilita' piu' bassa",
        motivo="verificare se i segnali meno netti pagano comunque i costi",
        modifiche={"signals.min_probability": 0.52},
    ),
    Candidato(
        nome="margine richiesto piu' stretto",
        motivo="accettare vantaggi piccoli ma ancora positivi",
        modifiche={"signals.min_expected_edge_bps": 2.0},
    ),
    Candidato(
        nome="orizzonte piu' lungo",
        motivo=(
            "su orizzonti brevi il movimento atteso e' piccolo rispetto ai costi; "
            "allungando, il movimento cresce e i costi restano"
        ),
        modifiche={"signals.signal_horizon_ms": 900_000},
    ),
    Candidato(
        nome="raffreddamento piu' corto",
        motivo="verificare se rientrare prima aggiunge o toglie",
        modifiche={"signals.cooldown_s": 15},
    ),
)
