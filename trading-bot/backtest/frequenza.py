"""Quante operazioni produce un'impostazione, e quanto costano.

"Voglio che operi in continuazione" merita un numero, non un'opinione.
Un giro completo costa circa 22 punti base fra commissioni e spread:
cento operazioni al giorno sono il 22% del controvalore scambiato,
ogni giorno. Qui si misura, sui dati dell'utente, quante operazioni
produce ogni impostazione, che risultato danno e cosa le sta fermando.

Quello che questo modulo non fa: comprare frequenza abbassando le
protezioni. Tutti i profili restano sopra il valore atteso zero, perche'
sotto quella riga il bot paga per operare, e piu' opera piu' paga.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from backtest.costs import CostModel
from backtest.engine import BacktestEngine
from backtest.metrics import Verdict, assess
from config.schema import AppConfig, CostsConfig
from core.types import Candle, Trade
from signals.base import SignalGenerator

__all__ = [
    "PROFILI",
    "EsitoCoppia",
    "EsitoFrequenza",
    "EsitoOrizzonte",
    "ProfiloFrequenza",
    "aggrega",
    "esplora",
    "esplora_coppie",
    "esplora_orizzonti",
]

_UN_GIORNO_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class ProfiloFrequenza:
    """Un punto sulla scala fra prudenza e frequenza."""

    nome: str
    descrizione: str
    #: Probabilita' calibrata minima perche' valga la pena operare.
    min_probability: float
    #: Margine richiesto OLTRE i costi. Zero significa "pareggio atteso",
    #: non "operare comunque": sotto zero non si scende.
    min_expected_edge_bps: float
    #: Attesa dopo un segnale sullo stesso simbolo.
    cooldown_s: int


#: La scala che il pannello mostra. Ogni gradino chiede meno del
#: precedente, cosi' la tabella si legge dall'alto verso il basso come
#: "piu' operazioni, meno selezione".
PROFILI: tuple[ProfiloFrequenza, ...] = (
    ProfiloFrequenza(
        nome="prudente",
        descrizione="opera solo quando il vantaggio e' netto. Poche operazioni.",
        min_probability=0.58,
        min_expected_edge_bps=10.0,
        cooldown_s=300,
    ),
    ProfiloFrequenza(
        nome="equilibrato",
        descrizione="l'impostazione di partenza.",
        min_probability=0.55,
        min_expected_edge_bps=5.0,
        cooldown_s=60,
    ),
    ProfiloFrequenza(
        nome="attivo",
        descrizione="accetta vantaggi piu' piccoli: piu' operazioni, piu' commissioni.",
        min_probability=0.52,
        min_expected_edge_bps=2.0,
        cooldown_s=15,
    ),
    ProfiloFrequenza(
        nome="massima frequenza",
        descrizione=(
            "opera appena il vantaggio atteso pareggia i costi. Sotto questa "
            "riga non si va: il bot pagherebbe per operare."
        ),
        min_probability=0.50,
        min_expected_edge_bps=0.0,
        cooldown_s=0,
    ),
)


@dataclass(frozen=True, slots=True)
class EsitoFrequenza:
    """Cosa succede con un profilo, sui dati dati."""

    profilo: ProfiloFrequenza
    giorni: float
    ordini: int
    operazioni: int
    operazioni_al_giorno: float
    rendimento: float
    commissioni: Decimal
    #: Costo di un giro completo, in punti base: la riga da battere.
    costo_giro_bps: float
    #: Quanto costerebbe, in percentuale del controvalore scambiato, la
    #: frequenza di questo profilo in un giorno.
    costo_giornaliero_pct: float
    verdetto: Verdict
    vincolo_principale: str

    def to_dict(self) -> dict[str, object]:
        return {
            "profilo": self.profilo.nome,
            "descrizione": self.profilo.descrizione,
            "probabilita_minima": self.profilo.min_probability,
            "margine_minimo_bps": self.profilo.min_expected_edge_bps,
            "attesa_s": self.profilo.cooldown_s,
            "giorni": round(self.giorni, 2),
            "ordini": self.ordini,
            "operazioni": self.operazioni,
            "operazioni_al_giorno": round(self.operazioni_al_giorno, 2),
            "rendimento": round(self.rendimento, 6),
            "commissioni": str(self.commissioni),
            "costo_giro_bps": round(self.costo_giro_bps, 2),
            "costo_giornaliero_pct": round(self.costo_giornaliero_pct, 3),
            "esito": self.verdetto.outcome,
            "spiegazione": self.verdetto.explanation,
            "vincolo_principale": self.vincolo_principale,
        }


def aggrega(candles: Sequence[Candle], fattore: int) -> list[Candle]:
    """Unisce ``fattore`` barre consecutive in una sola, per simbolo.

    Su barre piu' lunghe il movimento atteso cresce mentre il costo di un
    giro resta lo stesso: e' la leva che decide se una strategia puo'
    operare o no, e si misura invece di supporla.

    Una barra incompleta in fondo si scarta: quindici minuti costruiti con
    sette minuti di dati non sono quindici minuti.
    """
    if fattore <= 1:
        return list(candles)

    per_simbolo: dict[str, list[Candle]] = {}
    for candela in sorted(candles, key=lambda c: (c.symbol, c.open_time_ms)):
        per_simbolo.setdefault(candela.symbol, []).append(candela)

    unite: list[Candle] = []
    for symbol, serie in per_simbolo.items():
        for inizio in range(0, len(serie) - fattore + 1, fattore):
            blocco = serie[inizio : inizio + fattore]
            unite.append(
                Candle(
                    symbol=symbol,
                    open_time_ms=blocco[0].open_time_ms,
                    close_time_ms=blocco[-1].close_time_ms,
                    open=blocco[0].open,
                    high=max(c.high for c in blocco),
                    low=min(c.low for c in blocco),
                    close=blocco[-1].close,
                    volume=sum((c.volume for c in blocco), Decimal(0)),
                    quote_volume=sum((c.quote_volume for c in blocco), Decimal(0)),
                    trades=sum(c.trades for c in blocco),
                    is_closed=True,
                )
            )
    unite.sort(key=lambda c: (c.close_time_ms, c.symbol))
    return unite


def _con_profilo(config: AppConfig, profilo: ProfiloFrequenza) -> AppConfig:
    """Copia della configurazione con le sole soglie del profilo cambiate.

    Si cambiano le soglie del segnale, non i limiti di rischio: esposizione
    massima, ordine massimo, stop e kill switch restano quelli scelti
    dall'utente. La frequenza e' una scelta, le protezioni no.
    """
    raw = config.model_dump()
    segnali = dict(raw["signals"])
    segnali["min_probability"] = profilo.min_probability
    segnali["min_expected_edge_bps"] = profilo.min_expected_edge_bps
    segnali["cooldown_s"] = profilo.cooldown_s
    raw["signals"] = segnali
    return AppConfig.model_validate(raw)


def _vincolo(motivi: Sequence[str]) -> str:
    """Il motivo di rifiuto piu' frequente, ripulito dai numeri.

    "edge netto -15.6 bps" e "edge netto -15.4 bps" sono lo stesso
    problema: tenerli separati produce un elenco di varianti invece di una
    diagnosi.
    """
    if not motivi:
        return ""
    conteggio: Counter[str] = Counter()
    esempi: dict[str, str] = {}
    for motivo in motivi:
        testa = motivo.split("(")[0].strip()
        parole = [p for p in testa.split() if not any(c.isdigit() for c in p)]
        classe = " ".join(parole)[:80]
        conteggio[classe] += 1
        esempi.setdefault(classe, motivo)
    classe, quante = conteggio.most_common(1)[0]
    # Si restituisce un esempio con i numeri dentro: "edge netto sotto la
    # soglia" non dice di quanto, e di quanto e' l'unica cosa che serve
    # sapere per decidere cosa cambiare.
    return f"{esempi[classe]} ({quante} volte)"


@dataclass(frozen=True, slots=True)
class EsitoOrizzonte:
    """Lo stesso profilo misurato su barre di durata diversa."""

    etichetta: str
    fattore: int
    esito: EsitoFrequenza

    def to_dict(self) -> dict[str, object]:
        riga = dict(self.esito.to_dict())
        riga["orizzonte"] = self.etichetta
        riga["fattore"] = self.fattore
        return riga


def esplora_orizzonti(
    config: AppConfig,
    candles: Sequence[Candle],
    trades: Sequence[Trade] = (),
    *,
    fattori: Sequence[int] = (1, 5, 15, 60),
    profilo: ProfiloFrequenza | None = None,
    generatore: Callable[[AppConfig], SignalGenerator] | None = None,
) -> list[EsitoOrizzonte]:
    """Misura lo stesso profilo su barre di durata crescente.

    E' l'altra meta' della domanda sulla frequenza: su barre da un minuto
    il movimento atteso e' piccolo rispetto ai costi, su barre piu' lunghe
    cresce. Spesso e' qui, non nelle soglie, che si decide se la strategia
    possa operare.
    """
    scelto = profilo or PROFILI[1]
    minime = config.features.warmup_bars + 50
    righe: list[EsitoOrizzonte] = []
    for fattore in fattori:
        barre = aggrega(candles, fattore)
        if len(barre) < minime:
            # Una misura su meno barre del warmup non e' una misura.
            continue
        etichetta = "1 barra" if fattore == 1 else f"{fattore} barre"
        esito = esplora(
            config,
            barre,
            trades,
            profili=[scelto],
            generatore=generatore,
        )[0]
        righe.append(EsitoOrizzonte(etichetta=etichetta, fattore=fattore, esito=esito))
    return righe


@dataclass(frozen=True, slots=True)
class EsitoCoppia:
    """La stessa impostazione su una coppia diversa."""

    simbolo: str
    etichetta_orizzonte: str
    fattore: int
    esito: EsitoFrequenza

    def to_dict(self) -> dict[str, object]:
        riga = dict(self.esito.to_dict())
        riga["simbolo"] = self.simbolo
        riga["orizzonte"] = self.etichetta_orizzonte
        riga["fattore"] = self.fattore
        return riga


def _solo(config: AppConfig, simbolo: str) -> AppConfig:
    """Configurazione con una sola coppia attiva.

    Serve a misurare una coppia per volta: con due coppie attive e un
    limite di posizioni aperte, la seconda a volte non entra per colpa
    della prima, e il confronto non direbbe piu' niente sulla coppia.
    """
    raw = config.model_dump()
    raw["symbols"] = [dict(riga) for riga in raw["symbols"]]
    raw["risk"] = dict(raw["risk"])
    raw["risk"]["limits"] = dict(raw["risk"]["limits"])
    trovato = False
    for riga in raw["symbols"]:
        attiva = riga["symbol"] == simbolo
        riga["enabled"] = attiva
        trovato = trovato or attiva
    if not trovato:
        # Coppia non dichiarata in configurazione: si aggiunge solo per
        # la misura, con gli stessi limiti delle altre.
        modello = dict(raw["symbols"][0])
        modello.update(
            {
                "symbol": simbolo,
                "base_asset": simbolo.removesuffix(config.quote_asset) or "XXX",
                "quote_asset": config.quote_asset,
                "enabled": True,
            }
        )
        raw["symbols"].append(modello)
    # Con una coppia sola, un limite di due posizioni aperte e'
    # inefficace e la configurazione lo rifiuta, giustamente. Qui si
    # misura una coppia per volta, quindi il limite scende a una: e' la
    # stessa cosa che il limite vero farebbe in quello scenario.
    raw["risk"]["limits"]["max_open_positions"] = 1
    return AppConfig.model_validate(raw)


def esplora_coppie(
    config: AppConfig,
    candele_per_simbolo: Mapping[str, Sequence[Candle]],
    trades_per_simbolo: Mapping[str, Sequence[Trade]] | None = None,
    *,
    fattori: Sequence[int] = (1, 5, 15),
    profilo: ProfiloFrequenza | None = None,
    generatore: Callable[[AppConfig], SignalGenerator] | None = None,
) -> list[EsitoCoppia]:
    """Misura la stessa impostazione su coppie e durate di barra diverse.

    E' la leva piu' grossa delle tre, e la meno ovvia. Il costo di un
    giro e' lo stesso su qualunque coppia; il movimento atteso no. Una
    coppia che si muove il triplo supera la riga dei costi tre volte piu'
    spesso, senza che nessuna protezione venga toccata.
    """
    scelto = profilo or PROFILI[1]
    minime = config.features.warmup_bars + 50
    righe: list[EsitoCoppia] = []

    for simbolo, candele in sorted(candele_per_simbolo.items()):
        if not candele:
            continue
        cfg = _solo(config, simbolo)
        flusso = (trades_per_simbolo or {}).get(simbolo, ())
        for fattore in fattori:
            barre = aggrega(candele, fattore)
            if len(barre) < minime:
                continue
            esiti = esplora(
                cfg, barre, flusso, profili=[scelto], generatore=generatore
            )
            righe.append(
                EsitoCoppia(
                    simbolo=simbolo,
                    etichetta_orizzonte="1 barra" if fattore == 1 else f"{fattore} barre",
                    fattore=fattore,
                    esito=esiti[0],
                )
            )
    return righe


def esplora(
    config: AppConfig,
    candles: Sequence[Candle],
    trades: Sequence[Trade] = (),
    *,
    profili: Sequence[ProfiloFrequenza] = PROFILI,
    generatore: Callable[[AppConfig], SignalGenerator] | None = None,
) -> list[EsitoFrequenza]:
    """Misura ogni profilo sugli stessi dati e restituisce la tabella."""
    if not candles:
        raise ValueError("nessuna candela da esplorare")

    inizio = min(c.open_time_ms for c in candles)
    fine = max(c.close_time_ms for c in candles)
    giorni = max((fine - inizio) / _UN_GIORNO_MS, 1 / 24)

    if generatore is None:
        from runner import build_generator as _build

        generatore = _build

    esiti: list[EsitoFrequenza] = []
    for profilo in profili:
        cfg = _con_profilo(config, profilo)
        risultato = BacktestEngine(cfg, generatore(cfg)).run(candles, trades=trades)
        metriche = risultato.metrics

        costi = CostModel(cfg.backtest.costs if cfg.backtest else CostsConfig())
        costo_giro = costi.round_trip_bps(cfg.risk.sizing.target_order_quote)
        al_giorno = metriche.trades / giorni

        motivi = [
            motivo
            for decisione in risultato.decisions
            for motivo in decisione.rejection_reasons
        ]
        esiti.append(
            EsitoFrequenza(
                profilo=profilo,
                giorni=giorni,
                ordini=risultato.orders_submitted,
                operazioni=metriche.trades,
                operazioni_al_giorno=al_giorno,
                rendimento=metriche.total_return,
                commissioni=metriche.total_fees,
                costo_giro_bps=costo_giro,
                costo_giornaliero_pct=al_giorno * costo_giro / 100.0,
                verdetto=assess(metriche),
                vincolo_principale=_vincolo(motivi),
            )
        )
    return esiti
