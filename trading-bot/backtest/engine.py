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

import hashlib
import logging
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
from risk.plan import PianoDiUscita, piano_di_uscita
from signals.base import SignalGenerator, TradePlan

__all__ = ["BacktestEngine", "BacktestResult", "default_filters"]

logger = logging.getLogger("trading_bot.backtest")


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
    #: Ordini approvati che l'exchange simulato non avrebbe potuto eseguire
    #: al prezzo trovato alla barra dopo (fondi, posseduto, filtri).
    orders_unfilled: int = 0

    @property
    def total_return(self) -> float:
        if self.initial_equity <= 0:
            return 0.0
        return float(self.final_equity / self.initial_equity) - 1.0

    def summary(self) -> str:
        return (
            f"barre={self.bars_processed} ordini={self.orders_submitted} "
            f"rifiuti={self.orders_rejected} non eseguiti={self.orders_unfilled} "
            f"equity {self.initial_equity} -> "
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
        features = FeatureEngine(
            self.config.features, trade_capacity=self.config.data.buffer_capacity
        )
        # Ordine in volo e istante dal quale puo' essere eseguito: con una
        # latenza piu' lunga di una barra, l'esecuzione non e' alla barra
        # successiva ma a quella in cui l'ordine sarebbe davvero arrivato.
        pending: dict[str, tuple[Order, TimestampMs]] = {}
        ultimi_prezzi: dict[str, Decimal] = {}
        # Stop e target delle posizioni aperte, come nel bot vero: senza, la
        # simulazione esce al cambio del punteggio e misura una gestione del
        # rischio diversa da quella che gira dal vivo.
        piani: dict[str, PianoDiUscita] = {}
        piani_in_attesa: dict[str, TradePlan] = {}
        latenza_ms = self.costs.config.latency_ms

        def esposizione_in_volo() -> dict[str, Decimal]:
            """Ordini decisi e non ancora eseguiti: esposizione gia' impegnata.

            Senza questo, con "una posizione al massimo" il backtest ne apre
            due nella stessa barra, perche' entrambe le decisioni vedono il
            portafoglio ancora vuoto. Il live ha lo stesso problema, e la
            stessa soluzione: un backtest che non lo riproduce misura un bot
            diverso da quello che opera.
            """
            return {
                symbol: order.quantity
                * Decimal(str(order.metadata.get("reference_price", "0")))
                for symbol, (order, _) in pending.items()
            }

        risk = RiskManager.from_config(
            self.config, clock, self._client_order_id, esposizione_in_volo
        )
        fills: list[Fill] = []
        trades: list[TradeResult] = []
        equity_curve: list[tuple[TimestampMs, Decimal]] = [(clock.now_ms(), portfolio.equity)]
        submitted = 0
        rejected = 0
        non_eseguiti = 0

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
                in_volo = pending.get(candle.symbol)
                order = None
                if in_volo is not None and clock.now_ms() >= in_volo[1]:
                    order = pending.pop(candle.symbol)[0]
                if order is not None:
                    fill, motivo = self.try_execute(
                        order, candle, portfolio, clock.now_ms()
                    )
                    if fill is None:
                        non_eseguiti += 1
                        logger.info(
                            "ordine %s non eseguibile all'apertura: %s",
                            order.client_order_id,
                            motivo,
                        )
                    else:
                        fills.append(fill)
                        result = portfolio.apply_fill(fill)
                        if result is not None:
                            trades.append(result)
                        piano_atteso = piani_in_attesa.pop(candle.symbol, None)
                        posizione = portfolio.positions.get(candle.symbol)
                        if posizione is None or posizione.is_flat:
                            piani.pop(candle.symbol, None)
                        elif piano_atteso is not None:
                            piani[candle.symbol] = piano_di_uscita(
                                candle.symbol, posizione, piano_atteso, clock.now_ms()
                            )
                continue

            # --- chiusura barra: solo ora la candela e' osservabile
            portfolio.update_mark(candle.symbol, candle.close)
            portfolio.start_new_day(ms_to_datetime(candle.close_time_ms).strftime("%Y-%m-%d"))
            features.on_candle(candle)

            bar_return = features.bar_return(candle.symbol)
            if bar_return is not None:
                risk.limits.correlations.update(
                    candle.symbol, bar_return, candle.close_time_ms
                )

            equity_curve.append((clock.now_ms(), portfolio.equity))
            ultimi_prezzi[candle.symbol] = candle.close

            # --- lo stop vale anche qui: un backtest che resta investito
            # dopo uno stop misura una protezione che dal vivo non esiste.
            risk.killswitch.check(
                portfolio.snapshot(clock.now_ms()), now_ms=clock.now_ms()
            )
            if risk.killswitch.flatten_pending:
                chiusure = risk.flatten_all(
                    portfolio.snapshot(clock.now_ms()),
                    ultimi_prezzi,
                    {
                        symbol: self.filters.get(symbol)
                        or default_filters(symbol, fetched_at_ms=clock.now_ms())
                        for symbol in portfolio.positions
                    },
                )
                for chiusura in chiusure:
                    # Alla barra successiva, come ogni altro ordine: anche la
                    # chiusura d'emergenza paga lo scarto fra decisione ed
                    # esecuzione.
                    pending.setdefault(
                        chiusura.symbol, (chiusura, clock.now_ms() + latenza_ms)
                    )
                if not chiusure and not portfolio.snapshot(clock.now_ms()).open_positions:
                    risk.killswitch.mark_flattened()

            # --- stop e target: valgono prima di qualunque nuova decisione
            piano = piani.get(candle.symbol)
            posizione_corrente = portfolio.positions.get(candle.symbol)
            if piano is not None and (
                posizione_corrente is None or posizione_corrente.is_flat
            ):
                piani.pop(candle.symbol, None)
            elif (
                piano is not None
                and posizione_corrente is not None
                and candle.symbol not in pending
            ):
                livello = piano.violato_da(candle.close)
                if livello is not None:
                    uscita = risk.close_position(
                        symbol=candle.symbol,
                        quantity=posizione_corrente.quantity,
                        price=candle.close,
                        filters=self.filters.get(candle.symbol)
                        or default_filters(candle.symbol, fetched_at_ms=clock.now_ms()),
                        origine=livello,
                    )
                    if uscita is not None:
                        pending[candle.symbol] = (uscita, clock.now_ms() + latenza_ms)
                        piani.pop(candle.symbol, None)
                        submitted += 1
                        continue

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
                pending[candle.symbol] = (decision.order, clock.now_ms() + latenza_ms)
                if decision.order.side is Side.BUY:
                    piani_in_attesa[candle.symbol] = proposal.plan
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
            orders_unfilled=non_eseguiti,
        )

    def try_execute(
        self, order: Order, candle: Candle, portfolio: Portfolio, now_ms: TimestampMs
    ) -> tuple[Fill | None, str]:
        """Esegue l'ordine se l'exchange lo avrebbe accettato a questo prezzo.

        Fra la decisione e l'esecuzione passa una barra, e il prezzo puo'
        essere saltato: l'ordine deciso su 60.000 puo' trovarsi davanti un
        mercato a 6.000.000. Eseguirlo comunque fa spendere soldi che non ci
        sono e porta il conto simulato sotto zero, cosa che su un exchange a
        pronti non succede. Qui si ricontrolla con il prezzo vero: fondi,
        posseduto e filtri di coppia.
        """
        if self.assume_maker and self._ordine_limite_rifiutato(order):
            return None, (
                "ordine limite non eseguito: con l'ipotesi maker una quota "
                f"di {self.costs.config.limit_order_reject_rate:.0%} degli "
                "ordini non trova esecuzione al prezzo richiesto"
            )
        fill = self._execute(order, candle, now_ms)
        filters = (
            self.filters.get(order.symbol)
            or default_filters(order.symbol, fetched_at_ms=now_ms)
        ).per_ordine(order.order_type)
        problemi = list(filters.quantity_violations(order.quantity, fill.price))
        if order.side is Side.BUY:
            costo = order.quantity * fill.price + fill.fee
            if costo > portfolio.cash:
                problemi.append(
                    f"liquidita' insufficiente: servono {costo:.2f}, "
                    f"in cassa {portfolio.cash:.2f}"
                )
        else:
            posseduto = portfolio.positions.get(order.symbol)
            disponibile = Decimal(0) if posseduto is None else posseduto.quantity
            if order.quantity > disponibile:
                problemi.append(
                    f"vendita di {order.quantity} oltre il posseduto {disponibile}"
                )
        if problemi:
            return None, "; ".join(problemi)
        return fill, ""

    def _ordine_limite_rifiutato(self, order: Order) -> bool:
        """Vero se questo ordine limite non viene eseguito.

        La scelta e' deterministica, derivata dall'identificativo
        dell'ordine: due esecuzioni dello stesso backtest devono dare lo
        stesso risultato, altrimenti non si puo' confrontare niente.
        """
        quota = self.costs.config.limit_order_reject_rate
        if quota <= 0:
            return False
        digest = hashlib.sha256(order.client_order_id.encode("utf-8")).digest()
        estratto = int.from_bytes(digest[:8], "big") / float(1 << 64)
        return estratto < quota

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
