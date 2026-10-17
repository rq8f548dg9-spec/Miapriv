"""Pannello di controllo locale: il bot si usa dal browser, non dal terminale.

Server e bot girano nello stesso processo e nello stesso event loop: il
pannello non "parla" con il bot attraverso la rete, lo tiene in mano.

Tre proprieta' di sicurezza che il pannello NON puo' aggirare, per scelta:

* **si affaccia solo su 127.0.0.1**. Un'interfaccia che puo' muovere denaro
  non deve essere raggiungibile dalla rete locale, men che meno da internet;
* **non puo' passare a denaro reale**. Il passaggio a ``live`` resta una
  modifica manuale al file piu' una variabile d'ambiente: un bottone e'
  troppo facile da premere;
* **non mostra mai le chiavi API**. Le riceve, le scrive in ``.env``, e da
  quel momento risponde soltanto "configurate: si'".
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from aiohttp import web

from backtest.engine import BacktestEngine
from backtest.metrics import assess
from config.credentials import (
    TESTNET_KEY_VAR,
    TESTNET_SECRET_VAR,
    load_credentials,
    load_dotenv,
)
from config.schema import AppConfig, Mode, load_config
from core.types import TimestampMs, datetime_to_ms, ms_to_datetime
from data.store import ParquetStore
from monitoring.logger import to_jsonable
from runner import TradingBot, build_bot, build_generator

__all__ = [
    "BotSupervisor",
    "create_app",
    "friendly_validation_message",
    "run_ui",
    "same_origin_only",
]

logger = logging.getLogger("trading_bot.webui")

STATIC_DIR = Path(__file__).parent / "static"
#: Punti massimi conservati per il grafico: oltre, la pagina diventa lenta
#: senza mostrare nulla di piu'.
MAX_EQUITY_POINTS = 2_000
MAX_DECISIONS_SHOWN = 200


class UiError(Exception):
    """Errore da mostrare all'utente nel pannello, in italiano."""


@dataclass
class Job:
    """Operazione lunga (scarico dati, backtest) in corso o conclusa."""

    name: str
    state: str = "in corso"
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    @property
    def is_running(self) -> bool:
        return self.state == "in corso"

    def to_dict(self) -> dict[str, Any]:
        return {
            "nome": self.name,
            "stato": self.state,
            "messaggio": self.message,
            "risultato": self.result,
            "secondi": round(time.time() - self.started_at, 1),
        }


@dataclass
class BotSupervisor:
    """Possiede il bot: lo avvia, lo ferma, ne espone lo stato al pannello."""

    config_path: Path
    config: AppConfig
    bot: TradingBot | None = None
    task: asyncio.Task[None] | None = None
    sampler: asyncio.Task[None] | None = None
    equity_history: list[tuple[TimestampMs, str]] = field(default_factory=list)
    job: Job | None = None
    #: Riferimento al task del lavoro in corso: senza, il garbage collector
    #: puo' raccoglierlo a meta' e il lavoro sparisce in silenzio.
    job_task: asyncio.Task[None] | None = None
    last_error: str | None = None
    started_at_ms: TimestampMs | None = None

    # -- stato ------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self.task is not None and not self.task.done()

    def reload_config(self) -> AppConfig:
        self.config = load_config(self.config_path)
        return self.config

    def credentials_present(self) -> bool:
        if self.config.mode is Mode.BACKTEST:
            return True
        try:
            return load_credentials(self.config.mode) is not None
        except Exception:
            return False

    def stored_candles(self) -> dict[str, Any]:
        """Quante candele ci sono per simbolo: serve a sapere se si puo' testare."""
        store = ParquetStore(self.config.data.parquet_root)
        counts: dict[str, int] = {}
        for path in store.partitions("candles"):
            symbol = path.parent.name.removeprefix("symbol=")
            # il numero di righe e' nel nome del file: part-<ora>-<righe>.parquet
            parts = path.stem.rsplit("-", 1)
            counts[symbol] = counts.get(symbol, 0) + (
                int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
            )
        return {
            "per_simbolo": counts,
            "totale": sum(counts.values()),
            "cartella": str(self.config.data.parquet_root),
        }

    def status(self) -> dict[str, Any]:
        bot = self.bot
        payload: dict[str, Any] = {
            "modalita": self.config.mode.value,
            "denaro_reale": self.config.mode.uses_real_money,
            "in_esecuzione": self.is_running,
            "credenziali_presenti": self.credentials_present(),
            "simboli": [s.symbol for s in self.config.enabled_symbols],
            "capitale": str(self.config.capital_quote),
            "valuta": self.config.quote_asset,
            "generatore": self.config.signals.generator,
            "ultimo_errore": self.last_error,
            "lavoro": self.job.to_dict() if self.job else None,
            "avviato_ms": self.started_at_ms,
            "dati": self.stored_candles(),
        }

        if bot is None:
            payload.update(
                {
                    "equity": str(self.config.capital_quote),
                    "cash": str(self.config.capital_quote),
                    "pnl_realizzato": "0",
                    "pnl_non_realizzato": "0",
                    "commissioni": "0",
                    "posizioni": [],
                    "drawdown_giornaliero": 0.0,
                    "drawdown_totale": 0.0,
                    "perdite_consecutive": 0,
                    "killswitch": {"scattato": False, "motivo": None, "puo_aprire": True},
                    "decisioni_totali": 0,
                    "ordini_registrati": 0,
                }
            )
            return payload

        snapshot = bot.portfolio.snapshot(bot.clock.now_ms())
        last_event = bot.risk.killswitch.last_event
        payload.update(
            {
                "equity": str(snapshot.equity),
                "cash": str(snapshot.cash),
                "pnl_realizzato": str(bot.portfolio.realized_pnl),
                "pnl_non_realizzato": str(snapshot.unrealized_pnl),
                "commissioni": str(bot.portfolio.fees_paid),
                "posizioni": [
                    {
                        "simbolo": symbol,
                        "quantita": str(position.quantity),
                        "prezzo_carico": str(position.average_entry_price),
                        "prezzo_corrente": str(
                            bot.last_prices.get(symbol, position.average_entry_price)
                        ),
                        "valore": str(
                            position.notional(
                                bot.last_prices.get(symbol, position.average_entry_price)
                            )
                        ),
                        "pnl": str(
                            position.unrealized_pnl(
                                bot.last_prices.get(symbol, position.average_entry_price)
                            )
                        ),
                    }
                    for symbol, position in snapshot.open_positions.items()
                ],
                "drawdown_giornaliero": round(snapshot.daily_drawdown_pct, 4),
                "drawdown_totale": round(snapshot.total_drawdown_pct, 4),
                "perdite_consecutive": snapshot.consecutive_losses,
                "killswitch": {
                    "scattato": bot.risk.killswitch.is_tripped,
                    "motivo": last_event.describe() if last_event else None,
                    "puo_aprire": bot.risk.killswitch.allows_opening(bot.clock.now_ms()),
                },
                "decisioni_totali": len(bot.risk.decisions),
                "ordini_registrati": len(bot.registry),
                "messaggi_ricevuti": None,
            }
        )
        return payload

    def decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Ultime decisioni, dalla piu' recente. E' il cuore del pannello."""
        if self.bot is None:
            return []
        rows: list[dict[str, Any]] = []
        for decision in list(self.bot.risk.decisions)[-limit:][::-1]:
            signal = decision.signal
            rows.append(
                {
                    "ts_ms": decision.ts_ms,
                    "simbolo": decision.symbol,
                    "approvata": decision.approved,
                    "direzione": signal.direction.value if signal else None,
                    "probabilita": round(signal.probability, 4) if signal else None,
                    "motivi": list(decision.rejection_reasons),
                    "quantita": str(decision.order.quantity) if decision.order else None,
                    "feature": dict(signal.features) if signal else {},
                    "rischio": to_jsonable(decision.risk_snapshot),
                }
            )
        return rows

    # -- controllo --------------------------------------------------------

    async def start(self) -> None:
        if self.is_running:
            raise UiError("il bot e' gia' in esecuzione")
        self.reload_config()
        if self.config.mode is Mode.BACKTEST:
            raise UiError(
                "la modalita' e' 'backtest': per far girare il bot imposta 'testnet'"
            )
        if not self.credentials_present():
            raise UiError(
                "chiavi API mancanti: inseriscile nella sezione Connessione prima di avviare"
            )

        self.last_error = None
        self.bot = build_bot(self.config)
        self.started_at_ms = self.bot.clock.now_ms()
        self.equity_history.clear()
        self.task = asyncio.create_task(self._run_bot())
        self.sampler = asyncio.create_task(self._sample_equity())
        logger.info("bot avviato dal pannello in modalita' %s", self.config.mode.value)

    async def _run_bot(self) -> None:
        assert self.bot is not None
        try:
            await self.bot.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("il bot si e' fermato per un errore")

    async def _sample_equity(self) -> None:
        """Campiona l'equity per il grafico, a intervalli regolari."""
        while True:
            await asyncio.sleep(5)
            if self.bot is None:
                continue
            point = (self.bot.clock.now_ms(), str(self.bot.portfolio.equity))
            self.equity_history.append(point)
            if len(self.equity_history) > MAX_EQUITY_POINTS:
                del self.equity_history[: len(self.equity_history) - MAX_EQUITY_POINTS]

    async def stop(self) -> None:
        if self.bot is not None:
            self.bot.running = False
        for task in (self.sampler, self.task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.task = None
        self.sampler = None
        logger.info("bot fermato dal pannello")

    async def flatten(self) -> int:
        if self.bot is None:
            raise UiError("il bot non e' in esecuzione")
        before = len(self.bot.portfolio.positions)
        await self.bot.flatten_all()
        return before

    def reset_killswitch(self) -> bool:
        if self.bot is None:
            raise UiError("il bot non e' in esecuzione")
        return self.bot.risk.killswitch.reset(self.bot.clock.now_ms())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _json(payload: Any, status: int = 200) -> web.Response:
    return web.json_response(to_jsonable(payload), status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"errore": message}, status=status)


async def _read_json(request: web.Request) -> dict[str, Any]:
    # aiohttp non controlla il content-type in .json(): senza questo, un
    # modulo HTML di un altro sito (che puo' inviare solo text/plain) sarebbe
    # una richiesta valida per gli endpoint che leggono un corpo.
    if request.content_type != "application/json":
        raise UiError("richiesta non valida: atteso un corpo JSON")
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise UiError("richiesta non valida") from exc
    if not isinstance(payload, dict):
        raise UiError("richiesta non valida")
    return payload


#: Forma ammessa per una chiave API Binance. Tutto cio' che non rientra
#: qui non e' una chiave, e non deve finire in un file di configurazione.
_CHIAVE_VALIDA = re.compile(r"[A-Za-z0-9_-]{16,128}")


def write_credentials(env_path: Path, api_key: str, api_secret: str) -> None:
    """Scrive le chiavi testnet in ``.env``, preservando le altre righe.

    Non tocca mai le variabili live: il pannello non deve poterle impostare.
    """
    if not api_key or not api_secret:
        raise UiError("chiave e segreto sono entrambi obbligatori")

    # Validazione positiva, non lista nera. Vietare "\n" e "\r" non basta:
    # str.splitlines(), che rilegge il file, spezza anche su \x85, \u2028 e
    # altri sei caratteri. Una chiave che ne contiene uno diventerebbe una
    # riga in piu' nel .env, e quella riga puo' essere
    # I_UNDERSTAND_LIVE_TRADING_RISK=yes, cioe' la guardia sul denaro reale
    # disarmata senza che nessuno l'abbia scritta.
    for nome, valore in (("chiave", api_key), ("segreto", api_secret)):
        if not _CHIAVE_VALIDA.fullmatch(valore):
            raise UiError(
                f"{nome} non valida: sono ammessi solo lettere, cifre, trattino e "
                "trattino basso, da 16 a 128 caratteri. Copiala di nuovo da Binance."
            )

    lines: list[str] = []
    if env_path.is_file():
        lines = [
            line
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith((f"{TESTNET_KEY_VAR}=", f"{TESTNET_SECRET_VAR}="))
        ]
    lines.append(f"{TESTNET_KEY_VAR}={api_key}")
    lines.append(f"{TESTNET_SECRET_VAR}={api_secret}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_path.chmod(0o600)  # le chiavi non sono leggibili dagli altri utenti

    # Valgono subito, senza riavviare il pannello.
    os.environ[TESTNET_KEY_VAR] = api_key
    os.environ[TESTNET_SECRET_VAR] = api_secret


#: Campi modificabili dal pannello, con il percorso nello YAML.
EDITABLE_FIELDS: dict[str, tuple[str, ...]] = {
    "capitale": ("capital_quote",),
    "ordine_target": ("risk", "sizing", "target_order_quote"),
    "ordine_massimo": ("risk", "sizing", "max_order_quote"),
    "posizioni_massime": ("risk", "limits", "max_open_positions"),
    "esposizione_massima": ("risk", "limits", "max_gross_exposure_quote"),
    "drawdown_giornaliero": ("risk", "killswitch", "max_daily_drawdown_pct"),
    "drawdown_totale": ("risk", "killswitch", "max_total_drawdown_pct"),
    "perdite_consecutive": ("risk", "killswitch", "max_consecutive_losses"),
    "probabilita_minima": ("signals", "min_probability"),
    "edge_minimo_bps": ("signals", "min_expected_edge_bps"),
}


def apply_config_changes(raw: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Applica le modifiche del pannello al dizionario di configurazione.

    Solo i campi in ``EDITABLE_FIELDS``: il pannello non e' un editor di file,
    e la modalita' in particolare non e' modificabile da qui.
    """
    # Copia profonda: lo YAML contiene anche date, che json non sa serializzare.
    updated: dict[str, Any] = copy.deepcopy(raw)
    for key, value in changes.items():
        if key == "simboli_abilitati":
            wanted = {str(s).upper() for s in value}
            for symbol in updated["symbols"]:
                symbol["enabled"] = symbol["symbol"].upper() in wanted
            continue
        path = EDITABLE_FIELDS.get(key)
        if path is None:
            raise UiError(f"campo '{key}' non modificabile dal pannello")
        target: dict[str, Any] = updated
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
    return updated


#: Nomi tecnici tradotti nelle etichette che l'utente vede nel pannello.
#: Un messaggio di errore che cita "max_open_positions" chiede alla persona
#: di indovinare quale casella toccare.
NOMI_LEGGIBILI: dict[str, str] = {
    "capital_quote": "«Capitale che il bot può usare»",
    "max_gross_exposure_quote": "«Esposizione massima totale»",
    "max_exposure_pct_of_equity": "«Esposizione massima in percentuale»",
    "max_open_positions": "«Posizioni aperte al massimo»",
    "target_order_quote": "«Valore di un ordine»",
    "max_order_quote": "«Ordine massimo»",
    "min_order_quote": "«Valore minimo di un ordine»",
    "max_order_pct_of_equity": "quota massima del capitale per ordine",
    "max_daily_drawdown_pct": "«Stop se perdo in giornata»",
    "max_total_drawdown_pct": "«Stop se perdo in totale»",
    "max_consecutive_losses": "«Stop dopo N perdite di fila»",
    "min_probability": "«Probabilità minima per operare»",
    "min_expected_edge_bps": "«Vantaggio minimo dopo i costi»",
    "kelly_fraction": "frazione di Kelly",
}

#: Vincoli di pydantic riscritti in italiano corrente.
_VINCOLI: tuple[tuple[str, str], ...] = (
    (r"Input should be less than or equal to (\S+)", r"non può superare \1"),
    (r"Input should be greater than or equal to (\S+)", r"deve essere almeno \1"),
    (r"Input should be less than (\S+)", r"deve essere minore di \1"),
    (r"Input should be greater than (\S+)", r"deve essere maggiore di \1"),
    (r"Input should be a valid integer.*", "deve essere un numero intero"),
    (r"Input should be a valid number.*", "deve essere un numero"),
    (r"Field required", "manca un valore obbligatorio"),
    (r"Extra inputs are not permitted", "campo non riconosciuto"),
)


def friendly_validation_message(error: Exception) -> str:
    """Traduce un errore di validazione in una frase che l'utente puo' usare.

    Pydantic produce messaggi pensati per chi scrive il codice: citano il nome
    del modello, il valore completo in input e un link alla documentazione.
    Riversarli in pagina significa dire alla persona che ha sbagliato senza
    dirle cosa cambiare.
    """
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return str(error)

    frasi: list[str] = []
    for item in errors():
        testo = str(item.get("msg", "")).removeprefix("Value error, ")
        for pattern, replacement in _VINCOLI:
            testo = re.sub(pattern, replacement, testo)
        posizione = ".".join(str(part) for part in item.get("loc", ()))
        campo = NOMI_LEGGIBILI.get(posizione.rsplit(".", 1)[-1])
        frasi.append(f"{campo}: {testo}" if campo else testo)

    messaggio = "; ".join(dict.fromkeys(frasi)) or str(error)
    for tecnico, leggibile in NOMI_LEGGIBILI.items():
        messaggio = messaggio.replace(tecnico, leggibile)
    return messaggio


#: Metodi che cambiano lo stato. Le letture non vanno protette: senza
#: intestazioni CORS un altro sito non puo' leggerne la risposta.
STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Nomi con cui il pannello accetta di essere chiamato.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


@web.middleware
async def same_origin_only(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    """Rifiuta le richieste che cambiano stato partite da un altro sito.

    Il pannello non ha password, e va bene: ascolta solo su 127.0.0.1, quindi
    serve gia' accesso al computer. Ma il browser e' un ponte verso
    127.0.0.1: una pagina qualunque, aperta mentre il pannello gira, puo'
    inviare una POST a http://127.0.0.1:8777/api/avvia con un modulo HTML,
    senza bisogno di leggerne la risposta. Basterebbe a far partire il bot o
    a chiudere tutte le posizioni a mercato.

    Due controlli, entrambi su intestazioni che il browser imposta da solo e
    che una pagina non puo' falsificare:

    * ``Sec-Fetch-Site``, inviata dai browser moderni, dice se la richiesta
      nasce dalla stessa origine;
    * ``Origin``, presente su ogni POST cross-origin, deve coincidere con
      l'indirizzo su cui gira il pannello.
    """
    if request.method not in STATE_CHANGING_METHODS:
        return await handler(request)

    rifiuto = (
        "richiesta bloccata: arriva da un altro sito. Il pannello accetta "
        "comandi solo dalla propria pagina, aperta su questo computer."
    )

    # Il nome con cui siamo stati chiamati deve essere il nostro. Senza
    # questo controllo, un dominio dell'attaccante che risolve a 127.0.0.1
    # (DNS rebinding) diventa "stessa origine" per il browser, e Origin e
    # Sec-Fetch-Site tornano a dire quello che lui vuole.
    host = request.host.split(":")[0].lower()
    if host not in ALLOWED_HOSTS:
        logger.warning("richiesta rifiutata: Host inatteso %s", request.host)
        return _error(
            "richiesta bloccata: il pannello risponde solo a 127.0.0.1 e localhost.",
            403,
        )

    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None and fetch_site not in ("same-origin", "none"):
        logger.warning("richiesta cross-site rifiutata: Sec-Fetch-Site=%s", fetch_site)
        return _error(rifiuto, 403)

    origin = request.headers.get("Origin")
    if origin is not None and urlparse(origin).netloc != request.host:
        logger.warning("richiesta rifiutata: Origin %s diverso da %s", origin, request.host)
        return _error(rifiuto, 403)

    return await handler(request)


def create_app(config_path: Path, *, env_path: Path = Path(".env")) -> web.Application:
    """Costruisce l'applicazione web del pannello."""
    supervisor = BotSupervisor(config_path=config_path, config=load_config(config_path))
    app = web.Application(middlewares=[same_origin_only])
    app["supervisor"] = supervisor

    async def index(request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC_DIR / "index.html")

    async def status(request: web.Request) -> web.Response:
        return _json(supervisor.status())

    async def decisions(request: web.Request) -> web.Response:
        limit = min(int(request.query.get("limite", 50)), MAX_DECISIONS_SHOWN)
        return _json({"decisioni": supervisor.decisions(limit)})

    async def equity(request: web.Request) -> web.Response:
        return _json(
            {
                "punti": [
                    {"t": ts, "v": value} for ts, value in supervisor.equity_history
                ]
            }
        )

    async def read_raw_config() -> dict[str, Any]:
        """Legge lo YAML in un thread: l'I/O su file non blocca l'event loop."""
        text = await asyncio.to_thread(config_path.read_text, encoding="utf-8")
        raw: dict[str, Any] = yaml.safe_load(text)
        return raw

    async def get_config(request: web.Request) -> web.Response:
        raw = await read_raw_config()
        return _json(
            {
                "grezza": raw,
                "modificabili": sorted(EDITABLE_FIELDS),
                "simboli": [
                    {"simbolo": s["symbol"], "abilitato": s["enabled"]}
                    for s in raw["symbols"]
                ],
            }
        )

    async def put_config(request: web.Request) -> web.Response:
        if supervisor.is_running:
            return _error("ferma il bot prima di modificare la configurazione")
        try:
            changes = await _read_json(request)
            raw = await read_raw_config()
            updated = apply_config_changes(raw, changes)
            # Cintura e bretelle: l'elenco dei campi modificabili gia' esclude
            # la modalita', ma qui si verifica il risultato invece
            # dell'intenzione. model_validate non passa dalla guardia sul
            # denaro reale, quindi se un giorno l'elenco crescesse questa
            # riga resterebbe l'ultima difesa.
            if updated.get("mode") != raw.get("mode"):
                raise UiError("la modalita' non si cambia dal pannello")
            # Validazione prima della scrittura: un file non valido
            # impedirebbe anche di riaprire il pannello.
            AppConfig.model_validate(updated)
        except UiError as exc:
            return _error(str(exc))
        except Exception as exc:  # va mostrato nel pannello, non propagato
            return _error(friendly_validation_message(exc))

        await asyncio.to_thread(
            config_path.write_text,
            yaml.safe_dump(updated, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        supervisor.reload_config()
        return _json({"ok": True, "stato": supervisor.status()})

    async def set_credentials(request: web.Request) -> web.Response:
        try:
            payload = await _read_json(request)
            write_credentials(
                env_path,
                str(payload.get("api_key", "")).strip(),
                str(payload.get("api_secret", "")).strip(),
            )
        except UiError as exc:
            return _error(str(exc))
        return _json({"ok": True, "credenziali_presenti": supervisor.credentials_present()})

    async def test_connection(request: web.Request) -> web.Response:
        """Verifica che le chiavi funzionino davvero, prima di avviare."""
        from execution.binance import AiohttpClient, BinanceBroker

        try:
            credentials = load_credentials(supervisor.config.mode)
        except Exception as exc:
            return _error(f"chiavi non disponibili: {exc}")
        if credentials is None:
            return _error("la modalita' backtest non usa chiavi API")

        from core.clock import LiveClock

        broker = BinanceBroker(
            config=supervisor.config,
            credentials=credentials,
            clock=LiveClock(),
            http=AiohttpClient(),
        )
        try:
            filters = await broker.exchange_filters()
            state = await broker.account_state()
        except Exception as exc:
            return _error(f"connessione fallita: {exc}")
        finally:
            await broker.close()

        # La testnet accredita centinaia di asset finti: elencarli tutti
        # riempirebbe lo schermo di roba che al bot non serve. Si mostrano la
        # valuta di conto e le monete dei simboli abilitati.
        interessanti = {supervisor.config.quote_asset} | {
            s.base_asset for s in supervisor.config.enabled_symbols
        }
        saldi = {
            asset: str(amount)
            for asset, amount in sorted(state.balances.items())
            if amount > 0 and asset in interessanti
        }
        return _json(
            {
                "ok": True,
                "endpoint": supervisor.config.rest_url,
                "simboli_letti": sorted(filters),
                "saldi": saldi,
                "altri_asset": max(0, len(state.balances) - len(saldi)),
            }
        )

    async def start_bot(request: web.Request) -> web.Response:
        try:
            await supervisor.start()
        except UiError as exc:
            return _error(str(exc))
        except Exception as exc:
            return _error(f"avvio fallito: {exc}")
        return _json({"ok": True, "stato": supervisor.status()})

    async def stop_bot(request: web.Request) -> web.Response:
        await supervisor.stop()
        return _json({"ok": True, "stato": supervisor.status()})

    async def flatten(request: web.Request) -> web.Response:
        try:
            closed = await supervisor.flatten()
        except UiError as exc:
            return _error(str(exc))
        return _json({"ok": True, "posizioni": closed})

    async def reset_killswitch(request: web.Request) -> web.Response:
        try:
            done = supervisor.reset_killswitch()
        except UiError as exc:
            return _error(str(exc))
        if not done:
            return _error(
                "riarmo rifiutato: il periodo di attesa dopo lo stop non e' ancora finito"
            )
        return _json({"ok": True})

    async def fetch_data(request: web.Request) -> web.Response:
        if supervisor.job is not None and supervisor.job.is_running:
            return _error(f"c'e' gia' un'operazione in corso: {supervisor.job.name}")
        payload = await _read_json(request)
        days = max(1, min(int(payload.get("giorni", 30)), 365))

        job = Job(name=f"scarico dati ({days} giorni)")
        supervisor.job = job

        async def worker() -> None:
            from scripts.fetch_history import fetch

            try:
                total = await fetch(
                    [s.symbol for s in supervisor.config.enabled_symbols],
                    days,
                    config_path,
                )
                job.state = "completato"
                job.message = f"scaricate {total} candele"
                job.result = {"candele": total}
            except Exception as exc:
                job.state = "fallito"
                job.message = f"{type(exc).__name__}: {exc}"

        supervisor.job_task = asyncio.create_task(worker())
        return _json({"ok": True, "lavoro": job.to_dict()})

    async def run_backtest(request: web.Request) -> web.Response:
        if supervisor.job is not None and supervisor.job.is_running:
            return _error(f"c'e' gia' un'operazione in corso: {supervisor.job.name}")
        job = Job(name="backtest")
        supervisor.job = job

        def work() -> dict[str, Any]:
            config = supervisor.config
            if config.backtest is None:
                raise UiError("sezione 'backtest' assente in configurazione")
            store = ParquetStore(config.data.parquet_root)
            symbols = [s.symbol for s in config.enabled_symbols]

            # La finestra scritta nel file e' un default, mentre i dati
            # scaricati dal pannello sono "gli ultimi N giorni": se le due
            # cose non si sovrappongono il backtest non deve dire "scarica lo
            # storico" mentre lo storico c'e'. Si usa l'intersezione quando
            # esiste, altrimenti tutto quello che c'e', dicendo quale periodo
            # ha usato davvero.
            candles = store.read_candles(
                symbols=symbols,
                start_ms=datetime_to_ms(config.backtest.start),
                end_ms=datetime_to_ms(config.backtest.end),
            )
            window_note = "finestra indicata in configurazione"
            if not candles:
                candles = store.read_candles(symbols=symbols)
                window_note = "tutto lo storico disponibile"
            if not candles:
                raise UiError(
                    "nessuna candela salvata: esegui prima il passo 2 e scarica lo storico"
                )
            result = BacktestEngine(config, build_generator(config)).run(candles)
            metrics = result.metrics
            giudizio = assess(metrics)
            # I motivi si raggruppano per tipo, non per valore: "edge netto
            # -15.6 bps" e "edge netto -15.4 bps" sono lo stesso problema, e
            # tenerli separati produrrebbe un elenco illeggibile di varianti.
            motivi: dict[str, dict[str, Any]] = {}
            for decision in result.decisions:
                for reason in decision.rejection_reasons:
                    key = re.sub(r"[-+]?\d[\d.,]*", "N", reason.split("(")[0]).strip()[:90]
                    entry = motivi.setdefault(key, {"conteggio": 0, "esempio": reason})
                    entry["conteggio"] = int(entry["conteggio"]) + 1
            payload: dict[str, Any] = {
                "periodo": {
                    "da": ms_to_datetime(candles[0].open_time_ms).strftime("%d/%m/%Y"),
                    "a": ms_to_datetime(candles[-1].close_time_ms).strftime("%d/%m/%Y"),
                    "nota": window_note,
                },
                "barre": result.bars_processed,
                "ordini": result.orders_submitted,
                "rifiuti": result.orders_rejected,
                "equity_iniziale": str(result.initial_equity),
                "equity_finale": str(result.final_equity),
                "rendimento": round(result.total_return, 6),
                "operazioni": metrics.trades,
                "sharpe": metrics.sharpe,
                "sortino": metrics.sortino,
                "max_drawdown": round(metrics.max_drawdown, 6),
                "win_rate": metrics.win_rate,
                "profit_factor": metrics.profit_factor,
                "expectancy": str(metrics.expectancy),
                "commissioni": str(metrics.total_fees),
                "regge_i_costi": metrics.is_viable,
                "verdetto": {
                    "esito": giudizio.outcome,
                    "spiegazione": giudizio.explanation,
                },
                "motivi_rifiuto": [
                    {"esempio": entry["esempio"], "volte": entry["conteggio"]}
                    for _, entry in sorted(
                        motivi.items(),
                        key=lambda item: int(item[1]["conteggio"]),
                        reverse=True,
                    )[:8]
                ],
                "curva": [
                    {"t": ts, "v": str(value)}
                    for ts, value in result.equity_curve[
                        :: max(1, len(result.equity_curve) // 400)
                    ]
                ],
            }
            return payload

        async def worker() -> None:
            try:
                job.result = await asyncio.to_thread(work)
                job.state = "completato"
                # Tre esiti, non due: "pochi dati" non e' una bocciatura, e
                # annunciarlo come tale farebbe scartare una strategia che
                # non e' mai stata misurata davvero.
                verdetto = dict(job.result.get("verdetto", {}))
                job.message = str(
                    verdetto.get("spiegazione", "prova conclusa")
                )
            except Exception as exc:
                job.state = "fallito"
                job.message = f"{type(exc).__name__}: {exc}"

        supervisor.job_task = asyncio.create_task(worker())
        return _json({"ok": True, "lavoro": job.to_dict()})

    async def job_status(request: web.Request) -> web.Response:
        return _json({"lavoro": supervisor.job.to_dict() if supervisor.job else None})

    async def on_cleanup(app: web.Application) -> None:
        await supervisor.stop()

    app.router.add_get("/", index)
    app.router.add_get("/api/stato", status)
    app.router.add_get("/api/decisioni", decisions)
    app.router.add_get("/api/equity", equity)
    app.router.add_get("/api/config", get_config)
    app.router.add_post("/api/config", put_config)
    app.router.add_post("/api/credenziali", set_credentials)
    app.router.add_post("/api/test-connessione", test_connection)
    app.router.add_post("/api/avvia", start_bot)
    app.router.add_post("/api/ferma", stop_bot)
    app.router.add_post("/api/chiudi-tutto", flatten)
    app.router.add_post("/api/killswitch-reset", reset_killswitch)
    app.router.add_post("/api/scarica-dati", fetch_data)
    app.router.add_post("/api/backtest", run_backtest)
    app.router.add_get("/api/lavoro", job_status)
    app.router.add_static("/static/", STATIC_DIR)
    app.on_cleanup.append(on_cleanup)
    return app


def run_ui(
    config_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8777,
    env_path: Path = Path(".env"),
    open_browser: bool = True,
) -> None:
    """Avvia il pannello e, se possibile, apre il browser."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        # Un pannello che puo' muovere denaro non si espone sulla rete.
        raise ValueError(
            f"host {host!r} rifiutato: il pannello si affaccia solo su 127.0.0.1"
        )

    load_dotenv(env_path)
    app = create_app(config_path, env_path=env_path)
    url = f"http://{host}:{port}"
    print(f"\n  Pannello di controllo: {url}\n  (Ctrl+C per chiudere)\n")

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    web.run_app(app, host=host, port=port, print=None)
