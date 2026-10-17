"""Cablaggio del bot: mette insieme dati, feature, segnali, rischio, esecuzione.

E' l'unico modulo che conosce tutti gli altri. Gli altri non conoscono lui:
e' qui che si decide quale broker usare, quale generatore, dove scrivere lo
stato. Tenere il cablaggio in un posto solo e' quello che rende possibile
scambiare testnet con live cambiando una riga di configurazione invece di
inseguire condizionali sparsi nel codice.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from backtest.costs import CostModel
from config.credentials import Credentials, load_credentials
from config.schema import AppConfig, CostsConfig, Mode
from core.clock import Clock, LiveClock
from core.portfolio import Portfolio
from core.types import Candle, Fill, Order, OrderStatus, Side, SymbolFilters, TimestampMs, Trade
from data.ingest import MarketDataIngestor
from data.orderbook import ApplyResult, DepthUpdate, OrderBookState
from data.store import ParquetStore
from execution.binance import AiohttpClient, BinanceBroker
from execution.broker import Broker, BrokerError, PaperBroker
from execution.idempotency import DuplicateOrderError, OrderRegistry, client_order_id
from execution.reconcile import Reconciler
from features.engine import FeatureEngine
from monitoring.alerts import AlertManager, LogChannel
from monitoring.logger import DecisionLogger
from monitoring.state import StateStore
from risk.manager import MarketContext, RiskManager
from signals.base import SignalGenerator
from signals.rules import RuleBasedSignalGenerator

__all__ = ["TradingBot", "build_bot"]

logger = logging.getLogger("trading_bot.runner")


def build_generator(config: AppConfig) -> SignalGenerator:
    """Sceglie il generatore. Il modello ML resta un segnaposto che rifiuta."""
    if config.signals.generator == "ml":
        from signals.ml import MLSignalGenerator

        return MLSignalGenerator(
            model_path=config.signals.ml_model_path, horizon_ms=config.signals.signal_horizon_ms
        )
    return RuleBasedSignalGenerator(horizon_ms=config.signals.signal_horizon_ms)


def build_broker(
    config: AppConfig, clock: Clock, credentials: Credentials | None
) -> Broker:
    """Istanzia il broker della modalita' corrente.

    E' l'unico punto in cui si sceglie dove vanno gli ordini. La modalita'
    live e' gia' stata autorizzata dalla guardia al caricamento della
    configurazione: qui arriva solo se quel controllo e' passato.
    """
    if config.mode is Mode.BACKTEST:
        costs = config.backtest.costs if config.backtest else CostsConfig()
        return PaperBroker(
            clock=clock,
            costs=CostModel(costs),
            filters={},
            balances={config.quote_asset: config.capital_quote},
            quote_asset=config.quote_asset,
        )
    if credentials is None:
        raise ValueError(f"mode={config.mode.value} richiede credenziali API")
    return BinanceBroker(
        config=config, credentials=credentials, clock=clock, http=AiohttpClient()
    )


@dataclass
class TradingBot:
    """Il bot in esecuzione. Un'istanza, un processo, una modalita'."""

    config: AppConfig
    clock: Clock
    broker: Broker
    generator: SignalGenerator
    portfolio: Portfolio
    registry: OrderRegistry
    alerts: AlertManager
    decision_log: DecisionLogger
    state_store: StateStore
    store: ParquetStore | None = None

    features: FeatureEngine = field(init=False)
    risk: RiskManager = field(init=False)
    reconciler: Reconciler = field(init=False)
    books: dict[str, OrderBookState] = field(default_factory=dict)
    filters: dict[str, SymbolFilters] = field(default_factory=dict)
    last_prices: dict[str, Decimal] = field(default_factory=dict)
    needs_resync: set[str] = field(default_factory=set)
    last_order_latency_ms: float | None = None
    running: bool = False

    def __post_init__(self) -> None:
        self.features = FeatureEngine(self.config.features)
        self.risk = RiskManager.from_config(self.config, self.clock, self._next_order_id)
        self.reconciler = Reconciler(
            broker=self.broker,
            registry=self.registry,
            killswitch=self.risk.killswitch,
            quote_asset=self.config.quote_asset,
            symbols=tuple(s.symbol for s in self.config.enabled_symbols),
            max_capital=self.config.capital_quote,
        )
        for symbol_config in self.config.enabled_symbols:
            self.books[symbol_config.symbol] = OrderBookState(
                symbol_config.symbol, max_depth=self.config.data.orderbook_depth
            )

    # -- identificatori ---------------------------------------------------

    def _next_order_id(self, symbol: str, side: Side, ts_ms: TimestampMs) -> str:
        return client_order_id(
            symbol=symbol, side=side, quantity=Decimal(0), decision_ts_ms=ts_ms, intent="auto"
        )

    @staticmethod
    def _order_id_for(order_symbol: str, side: Side, quantity: Decimal, ts_ms: int) -> str:
        return client_order_id(
            symbol=order_symbol, side=side, quantity=quantity, decision_ts_ms=ts_ms
        )

    # -- avvio ------------------------------------------------------------

    async def startup(self) -> None:
        """Legge i filtri, risolve gli ordini in volo, ricostruisce lo stato.

        L'ordine conta: prima si sa cosa e' successo agli ordini in volo, poi
        si guarda il saldo, altrimenti si confronterebbe un saldo gia'
        aggiornato con un registro che non lo sa ancora.
        """
        self.filters = await self.broker.exchange_filters()
        logger.info("filtri letti da exchangeInfo per %d simboli", len(self.filters))

        resolved, fills = await self.reconciler.resolve_pending_orders()
        if resolved:
            logger.warning("risolti %d ordini rimasti in volo", len(resolved))
        for fill in fills:
            self.decision_log.log_fill(fill)

        if self.config.mode.touches_exchange:
            prices = await self._initial_prices()
            self.portfolio = await self.reconciler.rebuild_portfolio(prices=prices)
            self.last_prices.update(prices)

        self.decision_log.log_equity(self.clock.now_ms(), self.portfolio.equity, evento="avvio")

    async def _initial_prices(self) -> dict[str, Decimal]:
        """Prezzi correnti dal book, per valutare le posizioni esistenti."""
        prices: dict[str, Decimal] = {}
        for symbol_config in self.config.enabled_symbols:
            snapshot = await self.broker.depth_snapshot(symbol_config.symbol, limit=5)
            if snapshot is None:
                continue
            _, bids, asks = snapshot
            if bids and asks:
                prices[symbol_config.symbol] = (bids[0][0] + asks[0][0]) / Decimal(2)
        return prices

    # -- handler dei dati di mercato (interfaccia MarketDataHandler) -------

    def on_trade(self, trade: Trade) -> None:
        self.features.on_trade(trade)
        self.last_prices[trade.symbol] = trade.price
        self.risk.killswitch.record_data(trade.symbol, trade.ts_ms)
        if self.store is not None:
            self.store.add_trade(trade)

    def on_candle(self, candle: Candle) -> None:
        """Una candela chiusa e' il momento in cui si decide."""
        self.risk.killswitch.record_data(candle.symbol, candle.close_time_ms)
        if self.store is not None:
            self.store.add_candle(candle)
        if not candle.is_closed:
            return  # le candele in formazione si ignorano: conterrebbero il futuro

        self.features.on_candle(candle)
        self.portfolio.update_mark(candle.symbol, candle.close)
        self.last_prices[candle.symbol] = candle.close
        bar_return = self.features.bar_return(candle.symbol)
        if bar_return is not None:
            self.risk.limits.correlations.update(candle.symbol, bar_return)

    def on_depth(self, update: DepthUpdate) -> None:
        book = self.books.get(update.symbol)
        if book is None:
            return
        result = book.apply(update)
        if result is ApplyResult.GAP:
            # Buco di sequenza: il book non e' piu' attendibile.
            logger.warning("buco di sequenza su %s: richiesto nuovo snapshot", update.symbol)
            self.needs_resync.add(update.symbol)
            self.features.on_book(update.symbol, None)
            return
        if result is ApplyResult.BUFFERED:
            self.needs_resync.add(update.symbol)
            return
        self.features.on_book(update.symbol, book.snapshot())
        self.risk.killswitch.record_data(update.symbol, update.event_time_ms)

    def on_disconnect(self, ts_ms: TimestampMs, error: str) -> None:
        """Gli eventi persi durante la caduta non si recuperano: book invalidati."""
        self.risk.killswitch.record_reconnect(ts_ms)
        for symbol, book in self.books.items():
            book.invalidate("disconnessione")
            self.features.on_book(symbol, None)
            self.needs_resync.add(symbol)
        self.alerts.connection_lost(error)

    # -- ciclo di decisione -----------------------------------------------

    async def evaluate_symbol(self, symbol: str) -> Order | None:
        """Valuta un simbolo e, se approvato, invia l'ordine."""
        now_ms = self.clock.now_ms()
        vector = self.features.compute(symbol, now_ms)
        position = self.portfolio.positions.get(symbol)
        has_position = position is not None and not position.is_flat

        proposal = self.generator.generate(
            vector, has_position=has_position, now_ms=now_ms
        )
        if proposal is None:
            return None

        price = self.last_prices.get(symbol)
        symbol_filters = self.filters.get(symbol)
        if price is None or symbol_filters is None:
            logger.warning("nessun prezzo o filtro per %s: decisione saltata", symbol)
            return None

        costs = CostModel(self.config.backtest.costs if self.config.backtest else CostsConfig())
        decision = self.risk.evaluate(
            proposal.signal,
            self.portfolio.snapshot(now_ms),
            MarketContext(
                price=price,
                filters=symbol_filters,
                payoff_ratio=proposal.plan.payoff_ratio,
                expected_move_bps=proposal.plan.expected_move_bps,
                available_quote=self.portfolio.cash,
                last_order_latency_ms=self.last_order_latency_ms,
                estimated_cost_bps=costs.round_trip_bps(
                    self.config.risk.sizing.target_order_quote
                ),
            ),
        )
        self.decision_log.log_decision(decision)

        if not decision.approved or decision.order is None:
            return None
        await self.submit(decision.order)
        return decision.order

    async def submit(self, order: Order) -> None:
        """Invia un ordine passando dal registro: niente duplicati, mai."""
        deterministic = Order(
            client_order_id=self._order_id_for(
                order.symbol, order.side, order.quantity, order.created_at_ms
            ),
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            created_at_ms=order.created_at_ms,
            price=order.price,
            time_in_force=order.time_in_force,
            reduce_only=order.reduce_only,
            signal_ts_ms=order.signal_ts_ms,
            metadata=order.metadata,
        )
        try:
            self.registry.reserve(deterministic)
        except DuplicateOrderError as exc:
            logger.warning("invio bloccato dal registro: %s", exc)
            return

        started_ns = self.clock.monotonic_ns()
        try:
            ack = await self.broker.submit(deterministic)
        except BrokerError as exc:
            self.registry.update(deterministic.client_order_id, status=OrderStatus.REJECTED)
            self.alerts.order_rejected(deterministic.client_order_id, str(exc))
            return
        self.last_order_latency_ms = (self.clock.monotonic_ns() - started_ns) / 1_000_000

        self.registry.update(
            deterministic.client_order_id,
            status=ack.status,
            exchange_order_id=ack.exchange_order_id,
            filled_quantity=ack.filled_quantity,
        )
        if ack.status is OrderStatus.REJECTED:
            self.alerts.order_rejected(
                deterministic.client_order_id, str(ack.raw.get("motivo", "rifiutato"))
            )
            return
        for fill in ack.fills:
            self.apply_fill(fill, deterministic)

    def apply_fill(self, fill: Fill, order: Order | None = None) -> None:
        self.portfolio.apply_fill(fill)
        self.decision_log.log_fill(fill, order)
        self.decision_log.log_equity(fill.ts_ms, self.portfolio.equity)

    # -- compiti periodici -------------------------------------------------

    async def resync_books(self) -> None:
        """Riallinea i book che hanno perso la sequenza."""
        for symbol in list(self.needs_resync):
            snapshot = await self.broker.depth_snapshot(
                symbol, limit=self.config.data.orderbook_depth
            )
            if snapshot is None:
                continue
            last_update_id, bids, asks = snapshot
            book = self.books[symbol]
            book.apply_snapshot(
                last_update_id=last_update_id,
                bids=bids,
                asks=asks,
                ts_ms=self.clock.now_ms(),
            )
            self.features.on_book(symbol, book.snapshot())
            self.needs_resync.discard(symbol)
            logger.info("book di %s riallineato", symbol)

    async def check_killswitch(self) -> None:
        """Valuta le condizioni di arresto e, se serve, chiude tutto."""
        now_ms = self.clock.now_ms()
        events = self.risk.killswitch.check(
            self.portfolio.snapshot(now_ms),
            now_ms=now_ms,
            last_order_latency_ms=self.last_order_latency_ms,
        )
        for event in events:
            self.alerts.killswitch_triggered(event)

        if self.risk.killswitch.flatten_pending:
            await self.flatten_all()
            self.risk.killswitch.mark_flattened()

    async def flatten_all(self) -> None:
        """Chiude tutte le posizioni a mercato. Chiamata dal kill switch."""
        orders = self.risk.flatten_all(
            self.portfolio.snapshot(self.clock.now_ms()), self.last_prices, self.filters
        )
        logger.critical("chiusura forzata di %d posizioni", len(orders))
        for order in orders:
            await self.submit(order)

    async def periodic_reconcile(self) -> None:
        result = await self.reconciler.reconcile(
            self.portfolio, now_ms=self.clock.now_ms()
        )
        if not result.is_consistent:
            self.alerts.reconciliation_failed(result.describe())

    def snapshot_state(self, *, force: bool = False) -> bool:
        return self.state_store.save(
            self.state_store.capture(self.portfolio, self.risk.killswitch), force=force
        )

    # -- ciclo principale --------------------------------------------------

    async def run(self, *, max_iterations: int | None = None) -> None:
        """Avvia ingest e compiti periodici fino allo stop."""
        self.running = True
        await self.startup()

        ingestor = MarketDataIngestor(self.config, self.clock, self)
        ingest_task = asyncio.create_task(ingestor.run())
        try:
            await self._supervise(max_iterations=max_iterations)
        finally:
            self.running = False
            ingestor.stop()
            ingest_task.cancel()
            self.snapshot_state(force=True)
            if self.store is not None:
                self.store.flush()
            await self.broker.close()

    async def _supervise(self, *, max_iterations: int | None) -> None:
        """Compiti periodici: decisioni, kill switch, riconciliazione, stato."""
        iterations = 0
        last_reconcile_ms = self.clock.now_ms()
        while self.running:
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            try:
                await self.resync_books()
                for symbol_config in self.config.enabled_symbols:
                    await self.evaluate_symbol(symbol_config.symbol)
                await self.check_killswitch()

                now_ms = self.clock.now_ms()
                if now_ms - last_reconcile_ms >= self.config.monitoring.reconcile_interval_s * 1000:
                    await self.periodic_reconcile()
                    last_reconcile_ms = now_ms
                self.snapshot_state()
            except Exception as exc:
                logger.exception("errore nel ciclo principale")
                self.alerts.critical_error("ciclo principale", exc)
            await self.clock.sleep(1.0)


def build_bot(config: AppConfig, *, clock: Clock | None = None) -> TradingBot:
    """Costruisce un bot completo dalla configurazione validata."""
    active_clock = clock or LiveClock()
    credentials = load_credentials(config.mode)
    broker = build_broker(config, active_clock, credentials)

    return TradingBot(
        config=config,
        clock=active_clock,
        broker=broker,
        generator=build_generator(config),
        portfolio=Portfolio(config.capital_quote),
        registry=OrderRegistry(Path("state") / "orders.json"),
        alerts=AlertManager(
            active_clock,
            channels=[LogChannel()],
            enabled=config.monitoring.alerts_enabled,
        ),
        decision_log=DecisionLogger(mode=config.mode.value),
        state_store=StateStore(
            config.monitoring.state_path,
            active_clock,
            interval_s=config.monitoring.state_snapshot_s,
            mode=config.mode.value,
        ),
        store=ParquetStore(
            config.data.parquet_root, batch_size=config.data.write_batch_size
        ),
    )
