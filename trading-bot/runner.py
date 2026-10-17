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
from collections.abc import Awaitable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from backtest.costs import CostModel
from config.credentials import Credentials, load_credentials
from config.schema import AppConfig, CostsConfig, Mode
from core.clock import Clock, LiveClock
from core.portfolio import Portfolio
from core.types import (
    Candle,
    Fill,
    Order,
    OrderStatus,
    Position,
    Side,
    SymbolFilters,
    TimestampMs,
    Trade,
    ms_to_datetime,
)
from data.ingest import MarketDataIngestor
from data.orderbook import ApplyResult, DepthUpdate, OrderBookState
from data.store import ParquetStore
from execution.binance import AiohttpClient, AmbiguousRequestError, BinanceBroker
from execution.broker import Broker, BrokerError, PaperBroker
from execution.idempotency import DuplicateOrderError, OrderRegistry, client_order_id
from execution.reconcile import Reconciler
from features.engine import FeatureEngine
from monitoring.alerts import AlertManager, LogChannel, Severity
from monitoring.logger import DecisionLogger
from monitoring.state import StateStore
from risk.manager import MarketContext, RiskManager
from risk.plan import PianoDiUscita, piano_di_uscita
from signals.base import SignalGenerator, TradePlan
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
    #: Stop e target delle posizioni aperte, per simbolo.
    active_plans: dict[str, PianoDiUscita] = field(default_factory=dict)
    _saved_peak_equity: Decimal | None = field(default=None, repr=False)
    _saved_day_start_equity: Decimal | None = field(default=None, repr=False)
    _day_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.features = FeatureEngine(self.config.features)
        self.risk = RiskManager.from_config(
            self.config, self.clock, self._next_order_id, self.pending_exposure
        )
        # Le monete gia' presenti sul conto si rileggono dallo stato salvato:
        # senza, dopo un riavvio il bot scambierebbe la propria posizione per
        # roba di qualcun altro e smetterebbe di gestirla.
        saved = self.state_store.load()
        baseline = (
            {symbol: Decimal(q) for symbol, q in saved.baseline_holdings.items()}
            if saved is not None and saved.baseline_holdings
            else None
        )
        if saved is not None:
            # Le protezioni si ripristinano: un riavvio non e' un modo per
            # azzerare uno stop, un massimo storico o un drawdown in corso.
            self.risk.killswitch.restore(
                tripped_at_ms=saved.killswitch_tripped_at_ms,
                reason=saved.killswitch_reason,
            )
            self._saved_peak_equity = saved.peak_equity
            self._saved_day_start_equity = saved.day_start_equity
            # Applicati subito, non solo dopo la ricostruzione dall'exchange:
            # in backtest e in carta quella ricostruzione non avviene, e i
            # riferimenti del drawdown ripartirebbero comunque da zero.
            self._restore_drawdown_references()
            # Senza i piani, dopo un riavvio la posizione resterebbe senza
            # rete: nessuno la chiuderebbe piu' allo stop con cui e' stata
            # aperta, e il rischio vero non somiglierebbe piu' a quello
            # dichiarato quando e' stata dimensionata.
            for symbol, piano in saved.exit_plans.items():
                try:
                    self.active_plans[symbol] = PianoDiUscita.from_dict(symbol, piano)
                except (KeyError, ValueError, ArithmeticError):
                    logger.error("piano di uscita illeggibile per %s, ignorato", symbol)

        self.reconciler = Reconciler(
            broker=self.broker,
            registry=self.registry,
            killswitch=self.risk.killswitch,
            quote_asset=self.config.quote_asset,
            symbols=tuple(s.symbol for s in self.config.enabled_symbols),
            max_capital=self.config.capital_quote,
            baseline_holdings=baseline,
        )
        for symbol_config in self.config.enabled_symbols:
            self.books[symbol_config.symbol] = OrderBookState(
                symbol_config.symbol, max_depth=self.config.data.orderbook_depth
            )

    def _restore_drawdown_references(self) -> None:
        """Rimette massimo storico e riferimento giornaliero salvati."""
        if self._saved_peak_equity is not None:
            self.portfolio.peak_equity = self._saved_peak_equity
        if self._saved_day_start_equity is not None:
            self.portfolio.day_start_equity = self._saved_day_start_equity

    # -- identificatori ---------------------------------------------------

    @staticmethod
    def _next_order_id(
        *, symbol: str, side: Side, quantity: Decimal, intent: str, ts_ms: TimestampMs
    ) -> str:
        """Identificativo deterministico, uguale nella decisione e nell'invio.

        Averne due diversi spezzava il filo fra la riga di log che motiva
        l'ordine e l'ordine che l'exchange ha ricevuto.
        """
        return client_order_id(
            symbol=symbol,
            side=side,
            quantity=quantity,
            decision_ts_ms=ts_ms,
            intent=intent,
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
        self.check_order_size_feasible()

        resolved, fills = await self.reconciler.resolve_pending_orders()
        if resolved:
            logger.warning("risolti %d ordini rimasti in volo", len(resolved))
        # Le esecuzioni ritrovate sono esposizione vera: vanno contabilizzate,
        # non solo scritte nei log. Dove lo stato si ricostruisce dai saldi
        # reali (qui sotto) il conto viene comunque rifatto dall'exchange;
        # dove non si puo' (carta, backtest) questa e' l'unica strada.
        for fill in fills:
            self.apply_fill(fill)

        if self.config.mode.touches_exchange:
            prices = await self._initial_prices()
            self.portfolio = await self.reconciler.rebuild_portfolio(prices=prices)
            self.last_prices.update(prices)
            self._restore_drawdown_references()
            # La base appena stabilita va salvata prima di qualunque ordine:
            # un crash qui la perderebbe, e al riavvio il bot adotterebbe
            # come propria la posizione che sta per aprire.
            self.snapshot_state(force=True)

        self.decision_log.log_equity(self.clock.now_ms(), self.portfolio.equity, evento="avvio")

    def check_order_size_feasible(self) -> list[str]:
        """Avvisa se l'ordine dichiarato e' piu' piccolo del minimo di coppia.

        E' il silenzio peggiore: il bot gira, non sbaglia nulla, e non opera
        mai. Ogni decisione verrebbe rifiutata dal dimensionamento, e chi
        guarda il pannello vedrebbe solo un bot fermo senza sapere che basta
        alzare un numero.
        """
        sizing = self.config.risk.sizing
        margine = Decimal(str(sizing.min_notional_safety_factor))
        problemi: list[str] = []
        for symbol_config in self.config.enabled_symbols:
            filters = self.filters.get(symbol_config.symbol)
            if filters is None:
                continue
            richiesto = filters.min_notional * margine
            if sizing.target_order_quote >= richiesto:
                continue
            problemi.append(
                f"{symbol_config.symbol}: ordine dichiarato "
                f"{sizing.target_order_quote} {self.config.quote_asset}, ma "
                f"l'exchange ne chiede almeno {filters.min_notional} e il margine "
                f"di sicurezza porta il minimo a {richiesto:.2f}. Finche' resta "
                f"cosi' nessun ordine su questa coppia potra' partire: alza "
                f"\"valore di un ordine\" ad almeno {richiesto:.2f}."
            )
        for problema in problemi:
            logger.error("%s", problema)
            self.alerts.notify(
                key=f"sizing:{problema.split(':')[0]}",
                title="ORDINE PIU' PICCOLO DEL MINIMO DELLA COPPIA",
                detail=problema,
                severity=Severity.CRITICAL,
            )
        return problemi

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
        # Il mark segue i trade, non solo le candele chiuse: con l'equity
        # ferma fino al minuto successivo, drawdown e limiti leggono uno
        # stato vecchio proprio quando il prezzo si muove in fretta.
        if not self.portfolio.positions.get(trade.symbol, Position(trade.symbol)).is_flat:
            self.portfolio.update_mark(trade.symbol, trade.price)
        # Segno di vita: conta quando il dato e' arrivato, non quale istante
        # dichiari. Un timestamp futuro nasconderebbe un feed fermo.
        self.risk.killswitch.record_data(trade.symbol, self.clock.now_ms())
        if self.store is not None:
            self.store.add_trade(trade)

    def on_candle(self, candle: Candle) -> None:
        """Una candela chiusa e' il momento in cui si decide."""
        self.risk.killswitch.record_data(candle.symbol, self.clock.now_ms())
        if self.store is not None:
            self.store.add_candle(candle)
        if not candle.is_closed:
            return  # le candele in formazione si ignorano: conterrebbero il futuro

        self.features.on_candle(candle)
        self.portfolio.update_mark(candle.symbol, candle.close)
        self.last_prices[candle.symbol] = candle.close
        bar_return = self.features.bar_return(candle.symbol)
        if bar_return is not None:
            self.risk.limits.correlations.update(
                candle.symbol, bar_return, candle.close_time_ms
            )

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
        self.risk.killswitch.record_data(update.symbol, self.clock.now_ms())

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

        # I filtri scadono. Rileggerli quando servono, invece di rifiutare e
        # aspettare il timer, evita che il bot resti bloccato fino al
        # riavvio: la chiamata e' in cache lato broker, quindi costa poco.
        symbol_filters = self.filters.get(symbol)
        stale = symbol_filters is not None and symbol_filters.is_stale(
            now_ms, self.config.exchange.exchange_info_refresh_s * 1000
        )
        if symbol_filters is None or stale:
            if await self._safely("rilettura filtri", self._refresh_filters()):
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
                data_age_ms=vector.values.get("candle_age_ms", float("inf")),
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
        self._aggiorna_piano(symbol, proposal.plan)
        return decision.order

    def _aggiorna_piano(self, symbol: str, plan: TradePlan) -> None:
        """Fissa stop e target sulla posizione, o li dimentica se e' chiusa.

        I livelli si calcolano sul prezzo di carico effettivo, non sul
        prezzo visto al momento della decisione: fra i due c'e' lo slippage,
        e lo stop va dove protegge davvero.
        """
        position = self.portfolio.positions.get(symbol)
        if position is None or position.is_flat:
            self.active_plans.pop(symbol, None)
            return
        if position.average_entry_price <= 0:
            return
        self.active_plans[symbol] = piano_di_uscita(
            symbol, position, plan, self.clock.now_ms()
        )

    async def check_protective_exits(self) -> int:
        """Chiude le posizioni che hanno toccato il loro stop o il target.

        E' il piano con cui la posizione e' stata aperta: Kelly ha
        dimensionato una scommessa che perde lo stop o vince il target, e
        se nessuno la chiude a quei livelli il rischio corso non e' quello
        misurato. Vale anche a kill switch fermo: chiudere e' sempre
        permesso.
        """
        chiuse = 0
        for symbol, piano in list(self.active_plans.items()):
            position = self.portfolio.positions.get(symbol)
            if position is None or position.is_flat:
                self.active_plans.pop(symbol, None)
                continue
            price = self.last_prices.get(symbol)
            if price is None:
                continue
            livello = piano.violato_da(price)
            if livello is None:
                continue
            symbol_filters = self.filters.get(symbol)
            if symbol_filters is None:
                logger.error("niente filtri per %s: impossibile chiudere allo %s", symbol, livello)
                continue
            order = self.risk.close_position(
                symbol=symbol,
                quantity=position.quantity,
                price=price,
                filters=symbol_filters,
                origine=livello,
            )
            if order is None:
                continue
            logger.info(
                "%s raggiunto su %s a %s (carico %s): chiusura",
                livello,
                symbol,
                price,
                piano.entry_price,
            )
            await self.submit(order)
            if self.portfolio.positions[symbol].is_flat:
                self.active_plans.pop(symbol, None)
                chiuse += 1
        return chiuse

    def pending_exposure(self) -> dict[str, Decimal]:
        """Controvalore degli acquisti inviati e non ancora conclusi.

        E' esposizione in arrivo: va contata prima che arrivi, altrimenti due
        decisioni prese nello stesso istante vedono entrambe il portafoglio
        vuoto e aprono due posizioni dove ne era permessa una.
        """
        riservato: dict[str, Decimal] = {}
        for symbol, quantity in self.registry.pending_quantities(Side.BUY).items():
            price = self.last_prices.get(symbol) or self.portfolio.mark_prices.get(symbol)
            if price is None:
                continue
            riservato[symbol] = quantity * price
        return riservato

    async def submit(self, order: Order) -> None:
        """Invia un ordine passando dal registro: niente duplicati, mai.

        L'ordine viaggia con l'identificativo che gli ha dato il risk manager:
        e' lo stesso che compare nella decisione registrata nei log.
        """
        deterministic = order
        try:
            self.registry.reserve(deterministic)
        except DuplicateOrderError as exc:
            logger.warning("invio bloccato dal registro: %s", exc)
            return

        started_ns = self.clock.monotonic_ns()
        try:
            ack = await self.broker.submit(deterministic)
        except AmbiguousRequestError as exc:
            # Non si sa se l'ordine sia arrivato. Marcarlo rifiutato lo
            # toglierebbe dagli ordini da risolvere, e se l'exchange lo
            # aveva eseguito quella posizione non la gestirebbe piu'
            # nessuno. Resta in sospeso: lo chiarira' la riconciliazione.
            logger.error("esito sconosciuto per %s: %s", deterministic.client_order_id, exc)
            self.alerts.notify(
                key=f"order:ambiguo:{deterministic.client_order_id}",
                title="ESITO DELL'ORDINE SCONOSCIUTO",
                detail=(
                    f"{deterministic.client_order_id} su {deterministic.symbol}: {exc}. "
                    "Resta in sospeso fino alla riconciliazione."
                ),
                severity=Severity.CRITICAL,
            )
            return
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
        """Contabilizza un'esecuzione, una volta sola.

        Lo stesso fill puo' arrivare due volte: nella risposta all'ordine e
        poi di nuovo dalla riconciliazione, che ripesca gli ordini parziali
        finche' restano aperti. Contarlo due volte raddoppierebbe la
        posizione sui libri senza che nulla sia cambiato sull'exchange.
        """
        if self.portfolio.has_applied(fill):
            logger.debug("fill %s gia' contabilizzato, ignorato", fill.trade_id)
            return
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
            # Si dichiara fatta solo se le posizioni sono davvero chiuse.
            # Marcarla dopo il tentativo lascerebbe esposizione aperta con il
            # kill switch convinto di aver gia' messo tutto al sicuro.
            rimaste = {
                symbol: str(position.quantity)
                for symbol, position in self.portfolio.positions.items()
                if not position.is_flat
            }
            if rimaste:
                logger.critical("chiusura forzata incompleta, restano: %s", rimaste)
                self.alerts.notify(
                    key="flatten:incompleto",
                    title="CHIUSURA FORZATA INCOMPLETA",
                    detail=f"posizioni ancora aperte dopo lo stop: {rimaste}",
                    severity=Severity.CRITICAL,
                )
            else:
                self.risk.killswitch.mark_flattened()

    async def flatten_all(self) -> None:
        """Chiude tutte le posizioni a mercato. Chiamata dal kill switch.

        Prima si annullano gli ordini ancora in volo: un acquisto inviato e
        non ancora eseguito riaprirebbe esposizione un istante dopo lo stop,
        e il bot non ha piu' intenzione di gestirla.
        """
        await self.cancel_pending_orders()
        orders = self.risk.flatten_all(
            self.portfolio.snapshot(self.clock.now_ms()), self.last_prices, self.filters
        )
        logger.critical("chiusura forzata di %d posizioni", len(orders))
        for order in orders:
            try:
                await self.submit(order)
            except Exception:
                # Un simbolo che non si chiude non deve impedire di chiudere
                # gli altri. Cosa e' rimasto aperto lo verifica chi ha
                # chiamato, guardando il portafoglio.
                logger.critical(
                    "chiusura di %s fallita, si prosegue con gli altri simboli",
                    order.symbol,
                    exc_info=True,
                )

    async def cancel_pending_orders(self) -> int:
        """Annulla sull'exchange gli ordini registrati e non ancora conclusi."""
        annullati = 0
        for order_id in self.registry.pending_ids():
            symbol = self.registry.symbol_of(order_id)
            if symbol is None:
                continue
            try:
                riuscito = await self.broker.cancel(symbol, order_id)
            except BrokerError as exc:
                logger.critical("annullamento di %s fallito: %s", order_id, exc)
                self.alerts.notify(
                    key=f"cancel:{order_id}",
                    title="ORDINE IN VOLO NON ANNULLATO",
                    detail=(
                        f"{order_id} su {symbol} puo' ancora eseguirsi dopo lo stop: "
                        f"{exc}"
                    ),
                    severity=Severity.CRITICAL,
                )
                continue
            if riuscito:
                self.registry.update(order_id, status=OrderStatus.CANCELED)
                annullati += 1
            else:
                # Non annullabile vuol dire gia' concluso: il suo destino lo
                # stabilisce la riconciliazione, non un'ipotesi.
                logger.warning("ordine %s non annullabile, gia' concluso?", order_id)
        if annullati:
            logger.critical("annullati %d ordini ancora in volo", annullati)
        return annullati

    async def periodic_reconcile(self) -> None:
        result = await self.reconciler.reconcile(
            self.portfolio, now_ms=self.clock.now_ms()
        )
        # Un ordine che si esegue mentre nessuno guarda (parziale, o risposta
        # persa) si scopre qui: la sua esecuzione va messa a libro subito,
        # altrimenti il portafoglio resta indietro e alla riconciliazione
        # successiva la differenza fa scattare il kill switch.
        for fill in result.applied_fills:
            self.apply_fill(fill)
        if not result.is_consistent:
            self.alerts.reconciliation_failed(result.describe())

    def snapshot_state(self, *, force: bool = False) -> bool:
        return self.state_store.save(
            self.state_store.capture(
                self.portfolio,
                self.risk.killswitch,
                baseline_holdings=self.reconciler.baseline_holdings,
                exit_plans={
                    symbol: piano.to_dict() for symbol, piano in self.active_plans.items()
                },
            ),
            force=force,
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
        last_filters_ms = self.clock.now_ms()

        while self.running:
            if max_iterations is not None and iterations >= max_iterations:
                break
            iterations += 1
            now_ms = self.clock.now_ms()

            # Il kill switch per primo e in un blocco suo: un guasto in un
            # altro compito non deve poter impedire il controllo che ferma
            # il bot. Prima erano tutti nello stesso try, in fila.
            await self._safely("kill switch", self.check_killswitch())

            # Giorno nuovo, riferimento del drawdown giornaliero nuovo.
            # Senza questo la soglia giornaliera misura dal primo avvio.
            day_key = ms_to_datetime(now_ms).strftime("%Y-%m-%d")
            if day_key != self._day_key:
                self._day_key = day_key
                self.portfolio.start_new_day(day_key)

            await self._safely("riallineamento book", self.resync_books())

            # Stop e target prima di qualunque nuova valutazione: una
            # posizione che ha toccato il suo livello va chiusa adesso, non
            # dopo aver deciso se aprirne un'altra.
            await self._safely("uscite protettive", self.check_protective_exits())

            for symbol_config in self.config.enabled_symbols:
                await self._safely(
                    f"valutazione {symbol_config.symbol}",
                    self.evaluate_symbol(symbol_config.symbol),
                )

            # I filtri scadono: senza rinnovo, dopo l'intervallo di refresh
            # il risk manager rifiuterebbe ogni ingresso fino al riavvio.
            if now_ms - last_filters_ms >= self.config.exchange.exchange_info_refresh_s * 1000:
                await self._safely("rilettura filtri", self._refresh_filters())
                last_filters_ms = now_ms

            if now_ms - last_reconcile_ms >= self.config.monitoring.reconcile_interval_s * 1000:
                await self._safely("riconciliazione", self.periodic_reconcile())
                last_reconcile_ms = now_ms

            self.snapshot_state()

            uscita = max_iterations is not None and iterations >= max_iterations
            if self.running and not uscita:
                await self.clock.sleep(1.0)

    async def _safely(self, nome: str, lavoro: Awaitable[Any]) -> bool:
        """Esegue un compito periodico isolandone i guasti.

        Restituisce ``False`` se e' fallito. Un compito rotto viene segnalato
        e saltato: non deve trascinarsi dietro gli altri, e men che meno i
        controlli di sicurezza.
        """
        try:
            await lavoro
        except Exception as exc:
            logger.exception("errore nel compito periodico: %s", nome)
            self.alerts.critical_error(nome, exc)
            return False
        return True

    async def _refresh_filters(self) -> None:
        """Rilegge i filtri da exchangeInfo. Il broker li tiene in cache."""
        self.filters = await self.broker.exchange_filters()


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
