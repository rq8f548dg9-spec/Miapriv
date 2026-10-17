"""Schema di configurazione: modelli pydantic v2 con validazione fail-fast.

Principio 3 del progetto: nessun valore hardcoded. Ogni numero che influenza
una decisione del bot sta nel file YAML e passa da qui.

Scelte difensive applicate a tutti i modelli:

* ``extra="forbid"``: una chiave sconosciuta nello YAML (tipicamente un refuso)
  fa fallire l'avvio invece di essere ignorata in silenzio.
* ``frozen=True``: la configurazione non muta a runtime. Un componente che
  volesse "aggiustare" un limite deve farlo in modo visibile, non mutando lo
  stato condiviso.
* Importi monetari in ``Decimal``, rapporti e percentuali in ``float``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config.live_guard import enforce_live_trading_guard

__all__ = [
    "AppConfig",
    "BacktestConfig",
    "ConfigError",
    "CostsConfig",
    "DataConfig",
    "ExchangeConfig",
    "FeaturesConfig",
    "KillSwitchConfig",
    "LimitsConfig",
    "Mode",
    "MonitoringConfig",
    "RiskConfig",
    "SignalsConfig",
    "SizingConfig",
    "SymbolConfig",
    "WalkForwardConfig",
    "load_config",
]

Positive = Annotated[Decimal, Field(gt=0)]
NonNegative = Annotated[Decimal, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]

#: Tetto non negoziabile sulla frazione di Kelly (principio: mai Kelly pieno).
MAX_KELLY_FRACTION: Final[float] = 0.25


class ConfigError(ValueError):
    """Configurazione non valida. Il bot non parte."""


class Mode(StrEnum):
    BACKTEST = "backtest"
    TESTNET = "testnet"
    LIVE = "live"

    @property
    def uses_real_money(self) -> bool:
        return self is Mode.LIVE

    @property
    def touches_exchange(self) -> bool:
        return self in (Mode.TESTNET, Mode.LIVE)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)


#: Host di produzione di Binance. Un endpoint di prova che finisce su uno di
#: questi non e' una prova: sono ordini veri su denaro vero.
PRODUCTION_HOST_SUFFIXES = (
    "binance.com",
    "binance.us",
    "binance.je",
    "binancezh.com",
    "binanceapi.com",
)


def _host_of(url: str) -> str:
    """Solo il nome dell'host, in minuscolo e senza porta."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return host.lower()


def _is_production_host(host: str) -> bool:
    if "testnet" in host:
        return False
    return any(
        host == suffisso or host.endswith(f".{suffisso}")
        for suffisso in PRODUCTION_HOST_SUFFIXES
    )


class ExchangeConfig(_Base):
    """Endpoint e rate limit. Gli URL testnet e live sono campi distinti:
    non esiste una stringa che si trasforma da uno all'altro per sbaglio."""

    name: str = "binance"
    testnet_rest_url: str = "https://testnet.binance.vision"
    testnet_ws_url: str = "wss://stream.testnet.binance.vision/stream"
    live_rest_url: str = "https://api.binance.com"
    live_ws_url: str = "wss://stream.binance.com:9443/stream"

    recv_window_ms: int = Field(default=5_000, ge=1_000, le=60_000)
    request_timeout_s: float = Field(default=10.0, gt=0, le=120)

    # throttling lato client: sempre piu' stretto dei limiti reali dell'exchange
    max_request_weight_per_minute: int = Field(default=1_000, ge=1)
    max_orders_per_10s: int = Field(default=20, ge=1)
    max_orders_per_day: int = Field(default=5_000, ge=1)

    backoff_initial_s: float = Field(default=1.0, gt=0)
    backoff_max_s: float = Field(default=60.0, gt=0)
    backoff_multiplier: float = Field(default=2.0, gt=1.0)
    max_retries: int = Field(default=5, ge=0, le=20)

    #: Ogni quanto rileggere exchangeInfo (filtri e notional minimi cambiano).
    exchange_info_refresh_s: int = Field(default=3_600, ge=60)

    @model_validator(mode="after")
    def _check_backoff(self) -> ExchangeConfig:
        if self.backoff_max_s < self.backoff_initial_s:
            raise ValueError("backoff_max_s deve essere >= backoff_initial_s")
        return self

    @model_validator(mode="after")
    def _check_hosts(self) -> ExchangeConfig:
        """Gli indirizzi di prova non possono essere quelli veri.

        La guardia sul denaro reale controlla la modalita', non l'indirizzo:
        con ``mode: testnet`` e l'endpoint di produzione il bot invierebbe
        ordini veri convinto di star provando, e nessun avviso comparirebbe.
        Qui il legame fra modalita' e host diventa esplicito.
        """
        for campo in ("testnet_rest_url", "testnet_ws_url"):
            host = _host_of(getattr(self, campo))
            if _is_production_host(host):
                raise ValueError(
                    f"{campo}: {host} e' un indirizzo di produzione di Binance. "
                    "In modalita' di prova gli ordini sarebbero veri. "
                    "Usa testnet.binance.vision oppure un indirizzo locale."
                )
        for campo in ("live_rest_url", "live_ws_url"):
            host = _host_of(getattr(self, campo))
            if "testnet" in host:
                raise ValueError(
                    f"{campo}: {host} e' un indirizzo di prova, ma e' dichiarato "
                    "come live. Un conto che si crede reale e opera sulla prova "
                    "misura qualcosa che non esiste."
                )
        return self

    def rest_url(self, mode: Mode) -> str:
        """URL REST per la modalita' indicata. Unico punto che sceglie fra i due."""
        if mode is Mode.LIVE:
            return self.live_rest_url
        return self.testnet_rest_url

    def ws_url(self, mode: Mode) -> str:
        if mode is Mode.LIVE:
            return self.live_ws_url
        return self.testnet_ws_url


class SymbolConfig(_Base):
    """Un simbolo negoziabile.

    I filtri dell'exchange (tick size, step size, MIN_NOTIONAL) NON stanno qui:
    si leggono da ``exchangeInfo`` a runtime. Qui stanno solo i nostri limiti,
    che sono sempre piu' stringenti di quelli dell'exchange.
    """

    symbol: str = Field(min_length=3, max_length=20)
    base_asset: str = Field(min_length=2, max_length=10)
    quote_asset: str = Field(min_length=2, max_length=10)
    enabled: bool = True
    max_position_quote: Positive

    @field_validator("symbol", "base_asset", "quote_asset")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class SizingConfig(_Base):
    """Dimensionamento posizione. Kelly frazionario, mai Kelly pieno."""

    kelly_fraction: float = Field(default=0.25, gt=0.0, le=MAX_KELLY_FRACTION)
    #: Notional nominale per ordine, prima dell'aggiustamento Kelly.
    target_order_quote: Positive
    #: Tetto assoluto per singolo ordine, qualunque cosa dica Kelly.
    max_order_quote: Positive
    #: Pavimento: sotto questo valore non vale la pena pagare le fee.
    min_order_quote: Positive
    #: Margine di sicurezza sopra il MIN_NOTIONAL dell'exchange (1.10 = +10%).
    min_notional_safety_factor: float = Field(default=1.10, ge=1.0, le=2.0)
    #: Quota massima dell'equity impegnabile in un singolo ordine.
    max_order_pct_of_equity: Fraction = 0.10

    @model_validator(mode="after")
    def _check_order_bounds(self) -> SizingConfig:
        if not self.min_order_quote <= self.target_order_quote <= self.max_order_quote:
            raise ValueError(
                "vincolo violato: min_order_quote <= target_order_quote <= max_order_quote "
                f"(min={self.min_order_quote}, target={self.target_order_quote}, "
                f"max={self.max_order_quote})"
            )
        return self


class LimitsConfig(_Base):
    """Limiti di portafoglio verificati prima di ogni ordine."""

    max_open_positions: int = Field(default=3, ge=1, le=50)
    max_gross_exposure_quote: Positive
    max_exposure_pct_of_equity: Fraction = 0.50
    #: Correlazione massima ammessa fra una nuova posizione e quelle aperte.
    max_pairwise_correlation: float = Field(default=0.80, ge=0.0, le=1.0)
    correlation_lookback_bars: int = Field(default=240, ge=30)
    #: Sotto questo numero di osservazioni la correlazione non e' stimabile:
    #: in dubbio si rifiuta, non si approva.
    min_correlation_samples: int = Field(default=60, ge=10)
    max_orders_per_symbol_per_hour: int = Field(default=12, ge=1)
    #: Tempo minimo fra due ordini sullo stesso simbolo.
    min_seconds_between_orders: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def _check_samples(self) -> LimitsConfig:
        if self.min_correlation_samples > self.correlation_lookback_bars:
            raise ValueError(
                "min_correlation_samples non puo' superare correlation_lookback_bars"
            )
        return self


class KillSwitchConfig(_Base):
    """Condizioni di arresto. Il kill switch puo' chiudere tutto e fermarsi.

    ``auto_resume`` e' ``False`` per scelta: dopo uno stop serve un essere umano
    che guardi i log prima di ripartire.
    """

    max_daily_drawdown_pct: float = Field(default=0.03, gt=0.0, le=1.0)
    max_total_drawdown_pct: float = Field(default=0.10, gt=0.0, le=1.0)
    max_consecutive_losses: int = Field(default=5, ge=1)
    #: Latenza ordine oltre la quale l'exchange e' considerato inaffidabile.
    max_order_latency_ms: int = Field(default=2_000, ge=50)
    #: Eta' massima dell'ultimo dato ricevuto prima di dichiarare il feed morto.
    max_data_staleness_ms: int = Field(default=10_000, ge=500)
    max_reconnects_per_hour: int = Field(default=10, ge=1)
    #: Se attivo, allo scatto il bot chiude tutte le posizioni a mercato.
    flatten_positions_on_trigger: bool = True
    #: Pausa minima dopo uno scatto, anche con auto_resume attivo.
    cooldown_minutes: int = Field(default=60, ge=0)
    auto_resume: bool = False

    @model_validator(mode="after")
    def _check_drawdowns(self) -> KillSwitchConfig:
        if self.max_daily_drawdown_pct > self.max_total_drawdown_pct:
            raise ValueError(
                "max_daily_drawdown_pct non puo' superare max_total_drawdown_pct: "
                "il limite giornaliero non scatterebbe mai prima di quello totale"
            )
        return self


class RiskConfig(_Base):
    sizing: SizingConfig
    limits: LimitsConfig
    killswitch: KillSwitchConfig


#: Intervalli di candela ammessi, con la loro durata in millisecondi.
#: E' la sola definizione: la configurazione li valida e lo scaricatore dello
#: storico li usa per avanzare nel tempo.
INTERVAL_MS: dict[str, int] = {
    "1s": 1_000, "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


class DataConfig(_Base):
    """Ingest, buffer e persistenza."""

    #: Profondita' del book richiesta allo stream differenziale.
    orderbook_depth: int = Field(default=20, ge=5, le=1000)
    orderbook_update_speed_ms: int = Field(default=100, ge=100, le=1000)
    #: Capienza del ring buffer per simbolo e per tipo di evento.
    buffer_capacity: int = Field(default=10_000, ge=100)
    candle_interval: str = "1m"
    #: Ogni quanto riallineare il book con uno snapshot REST.
    orderbook_resync_s: int = Field(default=300, ge=30)
    ws_ping_interval_s: int = Field(default=20, ge=5)
    ws_reconnect_initial_s: float = Field(default=1.0, gt=0)
    ws_reconnect_max_s: float = Field(default=60.0, gt=0)
    parquet_root: Path = Path("data_store")
    #: Quanti eventi accumulare prima di scrivere una partizione Parquet.
    write_batch_size: int = Field(default=5_000, ge=100)
    retention_days: int = Field(default=90, ge=1)

    @field_validator("candle_interval")
    @classmethod
    def _known_interval(cls, value: str) -> str:
        # L'elenco e' uno solo, condiviso con lo scaricatore dello storico:
        # due elenchi separati divergono, e l'utente lo scopre con un errore
        # a meta' del download.
        if value not in INTERVAL_MS:
            raise ValueError(f"candle_interval {value!r} non fra {sorted(INTERVAL_MS)}")
        return value


class FeaturesConfig(_Base):
    """Finestre di calcolo. Tutte espresse in numero di barre o secondi.

    Il feature engine calcola in streaming: ogni finestra qui dichiarata
    determina quanti dati storici servono prima che il bot possa decidere
    (``warmup_bars``). Prima del warmup il bot non opera.
    """

    returns_windows_bars: tuple[int, ...] = (1, 5, 15, 60)
    volatility_windows_bars: tuple[int, ...] = (15, 60, 240)
    atr_window_bars: int = Field(default=14, ge=2)
    #: Oltre questa eta' il book non entra piu' nelle feature. Il libro pesa
    #: per quasi meta' del punteggio delle regole: se il flusso depth si
    #: ferma mentre i trade continuano, il bot deciderebbe su uno spessore
    #: di libro che non esiste piu'.
    max_book_age_ms: int = Field(default=5_000, ge=500)
    zscore_window_bars: int = Field(default=60, ge=10)
    #: Finestra temporale per l'order flow imbalance, in millisecondi.
    ofi_windows_ms: tuple[int, ...] = (1_000, 5_000, 30_000)
    #: Livelli del book usati per depth e imbalance.
    book_depth_levels: int = Field(default=10, ge=1, le=100)

    @field_validator("returns_windows_bars", "volatility_windows_bars", "ofi_windows_ms")
    @classmethod
    def _positive_sorted(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value:
            raise ValueError("la finestra non puo' essere vuota")
        if any(v <= 0 for v in value):
            raise ValueError(f"finestre non positive in {value}")
        if len(set(value)) != len(value):
            raise ValueError(f"finestre duplicate in {value}")
        return tuple(sorted(value))

    @property
    def warmup_bars(self) -> int:
        """Barre necessarie prima che tutte le feature siano definite."""
        return max(
            max(self.returns_windows_bars),
            max(self.volatility_windows_bars),
            self.atr_window_bars,
            self.zscore_window_bars,
        )


class SignalsConfig(_Base):
    """Soglie di generazione del segnale.

    ``min_expected_edge_bps`` e' la soglia che il segnale deve superare DOPO i
    costi stimati: un segnale che non paga le fee non e' un segnale.
    """

    generator: str = "rules"
    min_probability: Fraction = 0.55
    min_expected_edge_bps: float = Field(default=5.0, ge=0.0)
    signal_horizon_ms: int = Field(default=300_000, gt=0)
    #: Secondi di attesa dopo un segnale sullo stesso simbolo.
    cooldown_s: int = Field(default=60, ge=0)
    #: Modello ML: resta disabilitato finche' la baseline a regole non regge i costi.
    ml_enabled: bool = False
    ml_model_path: Path | None = None

    @model_validator(mode="after")
    def _check_ml(self) -> SignalsConfig:
        if self.ml_enabled and self.ml_model_path is None:
            raise ValueError("ml_enabled=true richiede ml_model_path")
        if self.generator not in {"rules", "ml"}:
            raise ValueError(f"generator {self.generator!r} sconosciuto (attesi: rules, ml)")
        if self.generator == "ml" and not self.ml_enabled:
            raise ValueError("generator='ml' richiede ml_enabled=true")
        return self


class CostsConfig(_Base):
    """Costi di transazione simulati. Principio 5: mai risultati al netto delle fee.

    Lo slippage e' modellato come ``base + impact * sqrt(size / depth)``: la
    radice riflette l'impatto concavo osservato sui book reali.
    """

    #: Tariffa standard di Binance spot: 0,1% su entrambi i lati, cioe' 10
    #: bps per lato. Non e' un dettaglio: con il maker a 1 bp, come era
    #: prima, qualunque strategia basata su ordini limite sembrerebbe dieci
    #: volte piu' economica di com'e'. Chi ha un livello VIP abbassa questi
    #: numeri consapevolmente.
    maker_fee_bps: float = Field(default=10.0, ge=0.0)
    taker_fee_bps: float = Field(default=10.0, ge=0.0)
    #: Sconto sulle commissioni, fra 0 e 0,5. Pagarle in BNB vale il 25%
    #: (0.25). Resta una scelta da dichiarare: darlo per scontato
    #: significherebbe misurare un conto che non e' il proprio.
    fee_discount_pct: float = Field(default=0.0, ge=0.0, le=0.5)
    #: Slippage minimo applicato a ogni esecuzione aggressiva.
    base_slippage_bps: float = Field(default=1.0, ge=0.0)
    #: Coefficiente di impatto sul book.
    impact_coefficient_bps: float = Field(default=5.0, ge=0.0)
    #: Profondita' di riferimento (quote) per normalizzare l'impatto.
    reference_depth_quote: Positive = Decimal("10000")
    #: Latenza ordine simulata: il fill avviene sul book di T + latenza.
    latency_ms: int = Field(default=150, ge=0)
    #: Quota di ordini limite che si assume NON venga eseguita.
    limit_order_reject_rate: Fraction = 0.20

    @model_validator(mode="after")
    def _check_fees(self) -> CostsConfig:
        if self.maker_fee_bps > self.taker_fee_bps:
            raise ValueError(
                "maker_fee_bps > taker_fee_bps: inatteso su Binance, "
                "se e' voluto rimuovi questo controllo consapevolmente"
            )
        return self


class WalkForwardConfig(_Base):
    """Walk-forward: train su N, test su N+1, scorrimento.

    ``purge_bars`` ed ``embargo_bars`` servono contro il leakage: la finestra di
    test non deve contenere barre il cui esito dipende da dati di train.
    """

    train_days: int = Field(default=30, ge=1)
    test_days: int = Field(default=7, ge=1)
    step_days: int = Field(default=7, ge=1)
    #: Barre scartate fra train e test (orizzonte del target).
    purge_bars: int = Field(default=60, ge=0)
    #: Barre scartate dopo il test prima della finestra successiva.
    embargo_bars: int = Field(default=60, ge=0)
    min_trades_per_window: int = Field(default=20, ge=1)


class BacktestConfig(_Base):
    start: datetime
    end: datetime
    costs: CostsConfig = CostsConfig()
    walkforward: WalkForwardConfig = WalkForwardConfig()
    #: Capitale iniziale del backtest; se assente usa quello di AppConfig.
    initial_capital_quote: Positive | None = None

    @model_validator(mode="after")
    def _check_window(self) -> BacktestConfig:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError(
                "start ed end devono avere un fuso orario esplicito "
                "(es. 2026-01-01T00:00:00Z)"
            )
        if self.end <= self.start:
            raise ValueError(f"finestra di backtest vuota: end {self.end} <= start {self.start}")
        return self


class MonitoringConfig(_Base):
    log_level: str = "INFO"
    #: Log strutturato JSON, una riga per decisione.
    json_logs: bool = True
    log_file: Path | None = Path("logs/trading_bot.jsonl")
    #: Ogni quanto salvare uno snapshot dello stato su disco.
    state_snapshot_s: int = Field(default=60, ge=5)
    state_path: Path = Path("state/bot_state.json")
    #: Ogni quanto riconciliare lo stato locale con l'exchange.
    reconcile_interval_s: int = Field(default=300, ge=30)
    alerts_enabled: bool = True
    #: Canali di alert. Le credenziali dei canali stanno in .env, non qui.
    alert_channels: tuple[str, ...] = ("log",)

    @field_validator("log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"log_level {value!r} non valido")
        return level

    @field_validator("alert_channels")
    @classmethod
    def _valid_channels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        allowed = {"log", "telegram", "email", "file"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"canali di alert sconosciuti: {sorted(unknown)}")
        return value


class AppConfig(_Base):
    """Configurazione completa del bot."""

    mode: Mode
    #: Capitale di riferimento, nella valuta quote.
    capital_quote: Positive
    quote_asset: str = "USDT"
    symbols: tuple[SymbolConfig, ...]
    risk: RiskConfig
    data: DataConfig = DataConfig()
    features: FeaturesConfig = FeaturesConfig()
    signals: SignalsConfig = SignalsConfig()
    backtest: BacktestConfig | None = None
    monitoring: MonitoringConfig = MonitoringConfig()
    exchange: ExchangeConfig = ExchangeConfig()

    @field_validator("quote_asset")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("symbols")
    @classmethod
    def _unique_symbols(cls, value: tuple[SymbolConfig, ...]) -> tuple[SymbolConfig, ...]:
        if not value:
            raise ValueError("serve almeno un simbolo")
        names = [s.symbol for s in value]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"simboli duplicati: {sorted(duplicates)}")
        return value

    @property
    def enabled_symbols(self) -> tuple[SymbolConfig, ...]:
        return tuple(s for s in self.symbols if s.enabled)

    @model_validator(mode="after")
    def _check_coherence(self) -> AppConfig:
        enabled = self.enabled_symbols
        if not enabled:
            raise ValueError("nessun simbolo abilitato: il bot non avrebbe nulla da fare")

        wrong_quote = [s.symbol for s in enabled if s.quote_asset != self.quote_asset]
        if wrong_quote:
            raise ValueError(
                f"simboli con quote diversa da {self.quote_asset}: {wrong_quote}. "
                "Capitale e esposizione sarebbero sommati fra valute diverse."
            )

        limits = self.risk.limits
        sizing = self.risk.sizing

        # Spot senza leva: non si puo' esporre piu' del capitale.
        if limits.max_gross_exposure_quote > self.capital_quote:
            raise ValueError(
                f"max_gross_exposure_quote {limits.max_gross_exposure_quote} supera il "
                f"capitale {self.capital_quote}: implicherebbe leva finanziaria"
            )
        if sizing.max_order_quote > limits.max_gross_exposure_quote:
            raise ValueError(
                f"max_order_quote {sizing.max_order_quote} supera "
                f"max_gross_exposure_quote {limits.max_gross_exposure_quote}"
            )
        if limits.max_open_positions > len(enabled):
            raise ValueError(
                f"max_open_positions {limits.max_open_positions} supera i simboli "
                f"abilitati ({len(enabled)}): limite inefficace"
            )

        # Il tetto per singolo ordine deve essere raggiungibile dal capitale.
        max_by_equity = self.capital_quote * Decimal(str(sizing.max_order_pct_of_equity))
        if sizing.min_order_quote > max_by_equity:
            raise ValueError(
                f"min_order_quote {sizing.min_order_quote} supera il tetto per ordine "
                f"derivato dall'equity ({max_by_equity}): nessun ordine sarebbe mai valido"
            )

        total_symbol_cap = sum((s.max_position_quote for s in enabled), Decimal(0))
        if total_symbol_cap < sizing.min_order_quote:
            raise ValueError(
                "la somma dei max_position_quote e' inferiore al minimo ordine: "
                "nessuna posizione sarebbe apribile"
            )

        if self.mode is Mode.BACKTEST and self.backtest is None:
            raise ValueError("mode='backtest' richiede la sezione 'backtest'")

        return self

    @property
    def rest_url(self) -> str:
        return self.exchange.rest_url(self.mode)

    @property
    def ws_url(self) -> str:
        return self.exchange.ws_url(self.mode)

    def active_limits_summary(self) -> dict[str, object]:
        """Limiti attivi, per il log di avvio e per l'alert del kill switch."""
        return {
            "max_order_quote": str(self.risk.sizing.max_order_quote),
            "max_gross_exposure_quote": str(self.risk.limits.max_gross_exposure_quote),
            "max_open_positions": self.risk.limits.max_open_positions,
            "max_daily_drawdown_pct": self.risk.killswitch.max_daily_drawdown_pct,
            "max_total_drawdown_pct": self.risk.killswitch.max_total_drawdown_pct,
            "max_consecutive_losses": self.risk.killswitch.max_consecutive_losses,
        }


def load_config(path: str | Path = Path("config/config.yaml")) -> AppConfig:
    """Carica, valida e autorizza la configurazione. Unico ingresso ammesso.

    In modalita' ``live`` chiama la guardia di ``config.live_guard``: se
    l'autorizzazione esplicita manca, solleva e il processo non parte. La
    chiamata non e' protetta da try/except di proposito.

    Raises:
        ConfigError: file assente o contenuto non valido.
        LiveTradingNotAuthorizedError: ``mode: live`` senza autorizzazione.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"file di configurazione non trovato: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw: Any = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: atteso un mapping YAML, trovato {type(raw).__name__}")

    try:
        config = AppConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError e sottoclassi di ValueError
        raise ConfigError(f"{config_path}: configurazione non valida\n{exc}") from exc

    if config.mode.uses_real_money:
        enforce_live_trading_guard(
            capital_at_risk=config.capital_quote,
            quote_asset=config.quote_asset,
            active_limits=config.active_limits_summary(),
        )

    return config
