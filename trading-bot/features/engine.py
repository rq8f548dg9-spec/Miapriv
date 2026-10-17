"""Orchestratore del calcolo feature in streaming.

Il contratto di questo modulo e' il principio 2 del progetto: il vettore di
feature prodotto all'istante T usa solo dati osservabili a T.

Tre regole lo rendono vero:

1. le candele entrano solo se chiuse, e vengono considerate osservabili dal
   loro ``close_time_ms``, non dal loro ``open_time_ms``;
2. le finestre di order flow scartano i trade con timestamp successivo
   all'istante di decisione, anche se sono gia' arrivati nel buffer;
3. finche' il warmup non e' completo il vettore e' dichiarato incompleto e il
   generatore di segnali non opera.

Il backtest usa questa stessa classe. Se la usasse solo il live, le due
strade divergerebbero e nessun backtest varrebbe niente.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from config.schema import FeaturesConfig
from core.types import Candle, OrderBookSnapshot, TimestampMs, Trade
from features.microstructure import OrderFlowImbalance, book_features
from features.momentum import MomentumFeatures
from features.volatility import AverageTrueRange, RealizedVolatility

__all__ = ["FeatureEngine", "FeatureVector", "SymbolFeatureState"]


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Feature di un simbolo a un istante preciso.

    ``is_complete`` falso significa warmup non terminato: il vettore si puo'
    loggare ma non ci si puo' decidere sopra.
    """

    symbol: str
    ts_ms: TimestampMs
    values: Mapping[str, float]
    is_complete: bool
    bars_seen: int
    reference_price: Decimal | None

    def get(self, name: str, default: float = 0.0) -> float:
        return self.values.get(name, default)


@dataclass
class SymbolFeatureState:
    """Stato dei calcolatori per un singolo simbolo."""

    symbol: str
    config: FeaturesConfig
    volatility: RealizedVolatility = field(init=False)
    atr: AverageTrueRange = field(init=False)
    momentum: MomentumFeatures = field(init=False)
    order_flow: OrderFlowImbalance = field(init=False)
    bars_seen: int = 0
    last_close: Decimal | None = None
    last_candle_close_ms: TimestampMs = 0
    last_book: OrderBookSnapshot | None = None

    def __post_init__(self) -> None:
        self.volatility = RealizedVolatility(self.config.volatility_windows_bars)
        self.atr = AverageTrueRange(self.config.atr_window_bars)
        self.momentum = MomentumFeatures(
            self.config.returns_windows_bars, self.config.zscore_window_bars
        )
        self.order_flow = OrderFlowImbalance(self.config.ofi_windows_ms)

    def on_candle(self, candle: Candle) -> None:
        """Aggiorna lo stato con una candela CHIUSA.

        Una candela in formazione contiene il futuro rispetto al suo istante
        di apertura: farla entrare qui significherebbe decidere oggi con i
        prezzi di domani.
        """
        if not candle.is_closed:
            raise ValueError(
                f"candela {candle.symbol} @{candle.open_time_ms} non chiusa: "
                "il feature engine accetta solo candele chiuse (anti look-ahead)"
            )
        if candle.close_time_ms < self.last_candle_close_ms:
            raise ValueError(
                f"candela {candle.symbol} fuori ordine: chiusura "
                f"{candle.close_time_ms} precedente a {self.last_candle_close_ms}"
            )
        self.volatility.update(candle.close)
        self.atr.update(candle)
        self.momentum.update(candle.close)
        self.bars_seen += 1
        self.last_close = candle.close
        self.last_candle_close_ms = candle.close_time_ms

    def on_trade(self, trade: Trade) -> None:
        self.order_flow.update(trade)

    def on_book(self, book: OrderBookSnapshot | None) -> None:
        """Registra il book. ``None`` (book non allineato) cancella il precedente."""
        self.last_book = book

    def compute(self, now_ms: TimestampMs, warmup_bars: int) -> FeatureVector:
        values: dict[str, float] = {}
        values.update(self.volatility.features())
        values.update(self.momentum.features())
        values.update(self.order_flow.features(now_ms))
        values.update(book_features(self.last_book, self.config.book_depth_levels))

        if self.last_close is not None:
            atr_bps = self.atr.value_bps(self.last_close)
            if atr_bps is not None:
                values["atr_bps"] = atr_bps
            values["close"] = float(self.last_close)

        values["bars_seen"] = float(self.bars_seen)
        values["candle_age_ms"] = float(max(0, now_ms - self.last_candle_close_ms))

        return FeatureVector(
            symbol=self.symbol,
            ts_ms=now_ms,
            values=values,
            is_complete=self.bars_seen >= warmup_bars and "atr_bps" in values,
            bars_seen=self.bars_seen,
            reference_price=self.last_close,
        )


@dataclass
class FeatureEngine:
    """Calcolo feature per tutti i simboli, in streaming."""

    config: FeaturesConfig
    states: dict[str, SymbolFeatureState] = field(default_factory=dict)

    @property
    def warmup_bars(self) -> int:
        """Barre necessarie prima che il bot possa operare."""
        return self.config.warmup_bars

    def state_for(self, symbol: str) -> SymbolFeatureState:
        state = self.states.get(symbol)
        if state is None:
            state = SymbolFeatureState(symbol, self.config)
            self.states[symbol] = state
        return state

    def on_candle(self, candle: Candle) -> None:
        self.state_for(candle.symbol).on_candle(candle)

    def on_trade(self, trade: Trade) -> None:
        self.state_for(trade.symbol).on_trade(trade)

    def on_book(self, symbol: str, book: OrderBookSnapshot | None) -> None:
        self.state_for(symbol).on_book(book)

    def compute(self, symbol: str, now_ms: TimestampMs) -> FeatureVector:
        """Vettore di feature osservabile a ``now_ms``."""
        return self.state_for(symbol).compute(now_ms, self.warmup_bars)

    def compute_all(self, now_ms: TimestampMs) -> dict[str, FeatureVector]:
        return {
            symbol: state.compute(now_ms, self.warmup_bars)
            for symbol, state in self.states.items()
        }

    def bar_return(self, symbol: str) -> float | None:
        """Ultimo rendimento a una barra: alimenta il tracker di correlazione."""
        return self.state_for(symbol).momentum.ret(1)
