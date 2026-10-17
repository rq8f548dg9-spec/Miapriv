"""Motore di backtest event-driven.

Event-driven e non vettoriale per una ragione precisa: una pipeline
vettoriale calcola tutte le feature su tutta la serie e poi filtra, e in
quell'ordine e' banale che un'operazione legga, anche solo per distrazione,
un valore futuro. Qui gli eventi si consumano uno alla volta, in ordine
cronologico, e il tempo lo tiene un ``SimulatedClock`` che si rifiuta di
tornare indietro.

Il vincolo anti-look-ahead e' strutturale, non una convenzione:

* la decisione si prende alla CHIUSURA della barra i, quando quella barra e'
  finalmente osservabile;
* l'ordine si esegue all'APERTURA della barra i+1, mai al prezzo con cui e'
  stata presa la decisione;
* lo slippage e le fee si applicano a quel prezzo di apertura.

Il motore riusa gli stessi ``FeatureEngine``, ``SignalGenerator`` e
``RiskManager`` del live. Se usasse componenti diversi, il backtest
misurerebbe un sistema che non esiste.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from backtest.costs import CostModel
from backtest.metrics import PerformanceMetrics, compute_metrics
from config.schema import AppConfig
from core.clock import SimulatedClock
from core.portfolio import Portfolio, TradeResult
from core.types import (
    Candle,
    Decision,
    Fill,
    Order,
    Side,
    SymbolFilters,
    TimestampMs,
    ms_to_datetime,
)
from features.engine import FeatureEngine
from risk.manager import MarketContext, RiskManager
from signals.base import SignalGenerator

__all__ = ["BacktestEngine", "BacktestResult", "default_filters"]


def default_filters(symbol: str, *, fetched_at_ms: TimestampMs = 0) -> SymbolFilters:
    """Filtri generici per un backtest su dati storici senza exchangeInfo.

    Sono volutamente prudenti (MIN_NOTIONAL 5, step fine): in live i filtri
    veri si leggono dall'exchange, qui servono solo a non rendere il backtest
    piu' permissivo della realta'.
    """
    return SymbolFilters(
        symbol=symbol,
        tick_size=Decimal("0.01"),
        step_size=Decimal("0.00001"),
        min_quantity=Decimal("0.00001"),
        max_quantity=Decimal("9000"),
        min_notional=Decimal("5"),
        base_precision=5,
        quote_precision=2,
        fetched_at_ms=fetched_at_ms,
    )


@dataclass
class BacktestResult:
    """Esito completo di una simulazione, con tutto il necessario per l'autopsia."""

    initial_equity: Decimal
    final_equity: Decimal
    equity_curve: list[tuple[TimestampMs, Decimal]]
    fills: list[Fill]
    trades: list[TradeResult]
    decisions: list[Decision]
    metrics: PerformanceMetrics
    bars_processed: int
    orders_submitted: int
    orders_rejected: int

    @property
    def total_return(self) -> float:
        if self.initial_equity <= 0:
            return 0.0
        return float(self.final_equity / self.initial_equity) - 1.0

    def summary(self) -> str:
        return (
            f"barre={self.bars_processed} ordini={self.orders_submitted} "
            f"rifiuti={self.orders_rejected} equity {self.initial_equity} -> "
            f"{self.final_equity} | {self.metrics.summary()}"
        )


@dataclass
class BacktestEngine:
    """Simulatore a eventi su candele."""

    config: AppConfig
    generator: SignalGenerator
    costs: CostModel = field(init=False)
    filters: dict[str, SymbolFilters] = field(default_factory=dict)
    #: Esecuzione come taker: le strategie a segnale entrano a mercato.
    assume_maker: bool = False

    def __post_init__(self) -> None:
        costs_config = (
            self.config.backtest.costs if self.config.backtest else None
        )
        if costs_config is None:
            raise ValueError(
                "il backtest richiede la sezione 'backtest' in configurazione "
                "(contiene il modello dei costi)"
            )
        self.costs = CostModel(costs_config)

    def run(self, candles: Sequence[Candle]) -> BacktestResult:
        """Esegue la simulazione sulle candele date, in ordine cronologico."""
        if not candles:
            raise ValueError("nessuna candela da simulare")
        ordered = sorted(candles, key=lambda c: (c.close_time_ms, c.symbol))
        open_candles = [c for c in ordered if not c.is_closed]
        if open_candles:
            raise ValueError(
                f"{len(open_candles)} candele non chiuse nel dataset: "
                "il backtest accetta solo barre definitive (anti look-ahead)"
            )

        initial = (
            self.config.backtest.initial_capital_quote
            if self.config.backtest and self.config.backtest.initial_capital_quote
            else self.config.capital_quote
        )

        clock = SimulatedClock(ordered[0].open_time_ms)
        portfolio = Portfolio(initial)
        features = FeatureEngine(self.config.features)
        risk = RiskManager.from_config(self.config, clock, self._client_order_id)

        pending: dict[str, Order] = {}
        fills: list[Fill] = []
        trades: list[TradeResult] = []
        equity_curve: list[tuple[TimestampMs, Decimal]] = [(clock.now_ms(), portfolio.equity)]
        submitted = 0
        rejected = 0

        # Coda di eventi: ogni candela produce un'apertura e una chiusura, e i
        # due momenti si intrecciano fra simboli diversi. Costruire la coda
        # esplicitamente e' l'unico modo perche' il tempo avanzi in modo
        # monotono anche su piu' simboli: un orologio che arretra e' il
        # sintomo classico di un backtest che sbircia.
        events: list[tuple[TimestampMs, int, str, Candle]] = []
        for candle in ordered:
            events.append((candle.open_time_ms, 0, candle.symbol, candle))
            events.append((candle.close_time_ms, 1, candle.symbol, candle))
        events.sort(key=lambda item: (item[0], item[1], item[2]))

        for ts_ms, kind, _symbol, candle in events:
            clock.advance_to(ts_ms)

            if kind == 0:
                # --- apertura barra: si eseguono gli ordini decisi alla barra precedente
                order = pending.pop(candle.symbol, None)
                if order is not None:
                    fill = self._execute(order, candle, clock.now_ms())
                    fills.append(fill)
                    result = portfolio.apply_fill(fill)
                    if result is not None:
                        trades.append(result)
                continue

            # --- chiusura barra: solo ora la candela e' osservabile
            portfolio.update_mark(candle.symbol, candle.close)
            portfolio.start_new_day(ms_to_datetime(candle.close_time_ms).strftime("%Y-%m-%d"))
            features.on_candle(candle)

            bar_return = features.bar_return(candle.symbol)
            if bar_return is not None:
                risk.limits.correlations.update(candle.symbol, bar_return)

            equity_curve.append((clock.now_ms(), portfolio.equity))

            # --- decisione per la barra successiva
            if candle.symbol in pending:
                continue  # c'e' gia' un ordine in volo su questo simbolo
            vector = features.compute(candle.symbol, clock.now_ms())
            position = portfolio.positions.get(candle.symbol)
            proposal = self.generator.generate(
                vector,
                has_position=position is not None and not position.is_flat,
                now_ms=clock.now_ms(),
            )
            if proposal is None:
                continue

            symbol_filters = self.filters.get(candle.symbol) or default_filters(
                candle.symbol, fetched_at_ms=clock.now_ms()
            )
            notional_guess = self.config.risk.sizing.target_order_quote
            decision = risk.evaluate(
                proposal.signal,
                portfolio.snapshot(clock.now_ms()),
                MarketContext(
                    price=candle.close,
                    filters=symbol_filters,
                    payoff_ratio=proposal.plan.payoff_ratio,
                    data_age_ms=vector.values.get("candle_age_ms", float("inf")),
                    expected_move_bps=proposal.plan.expected_move_bps,
                    available_quote=portfolio.cash,
                    estimated_cost_bps=self.costs.round_trip_bps(
                        notional_guess, is_maker=self.assume_maker
                    ),
                ),
            )
            if decision.approved and decision.order is not None:
                pending[candle.symbol] = decision.order
                submitted += 1
            else:
                rejected += 1

        metrics = compute_metrics(
            equity_curve,
            trades,
            total_fees=portfolio.fees_paid,
            periods_per_year=self._periods_per_year(ordered),
            risk_per_trade_quote=self.config.risk.sizing.target_order_quote,
        )

        return BacktestResult(
            initial_equity=initial,
            final_equity=portfolio.equity,
            equity_curve=equity_curve,
            fills=fills,
            trades=trades,
            decisions=risk.decisions,
            metrics=metrics,
            bars_processed=len(ordered),
            orders_submitted=submitted,
            orders_rejected=rejected,
        )

    def _execute(self, order: Order, candle: Candle, now_ms: TimestampMs) -> Fill:
        """Esegue un ordine all'apertura della barra, con costi applicati.

        Il prezzo di riferimento e' l'apertura della barra successiva alla
        decisione: non e' un prezzo che la strategia ha potuto osservare
        quando ha deciso, ed e' esattamente questo che rende onesto il conto.
        """
        cost = self.costs.apply(
            side=order.side,
            reference_price=candle.open,
            quantity=order.quantity,
            is_maker=self.assume_maker,
        )
        return Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            price=cost.fill_price,
            quantity=order.quantity,
            fee=cost.fee,
            fee_asset=self.config.quote_asset,
            ts_ms=now_ms,
            is_maker=cost.is_maker,
            trade_id=f"bt-{order.client_order_id}",
            exchange_order_id=f"sim-{order.client_order_id}",
        )

    @staticmethod
    def _periods_per_year(candles: Sequence[Candle]) -> float:
        """Barre per anno, dedotte dalla durata effettiva delle candele."""
        first = candles[0]
        duration_ms = first.close_time_ms - first.open_time_ms + 1
        if duration_ms <= 0:
            return 525_600.0
        return 365.0 * 24 * 60 * 60 * 1000 / duration_ms

    @staticmethod
    def _client_order_id(
        *, symbol: str, side: Side, quantity: Decimal, intent: str, ts_ms: TimestampMs
    ) -> str:
        return f"bt-{symbol}-{side.value}-{intent}-{quantity}-{ts_ms}"
