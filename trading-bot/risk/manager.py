"""Risk manager: l'unico componente che puo' autorizzare un ordine.

Nessun modulo costruisce ordini per conto proprio. Il generatore di segnali
propone, il risk manager dispone, e ogni valutazione produce una ``Decision``
loggabile, approvata o rifiutata che sia.

Ordine dei controlli, dal piu' grave al piu' specifico:

1. kill switch  - il sistema e' autorizzato a operare?
2. intento      - l'ordine apre, aumenta, riduce o chiude?
3. sizing       - quanto, secondo Kelly frazionario e i filtri dell'exchange?
4. limiti       - il portafoglio lo sopporta?

Un ordine di chiusura salta i controlli che valgono solo per l'apertura:
ridurre il rischio deve restare sempre possibile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from config.schema import AppConfig
from core.clock import Clock
from core.portfolio import PortfolioSnapshot
from core.types import (
    Decision,
    Order,
    OrderType,
    Side,
    Signal,
    SignalDirection,
    SymbolFilters,
    TimeInForce,
    TimestampMs,
)
from risk.killswitch import KillSwitch
from risk.limits import (
    CorrelationTracker,
    LimitChecker,
    OrderIntent,
    OrderProposal,
    OrderRateTracker,
)
from risk.sizing import PositionSizer, SizingResult, kelly_fraction

__all__ = ["MarketContext", "RiskManager"]

class ClientOrderIdFactory(Protocol):
    """Genera l'identificativo di un ordine (cfr. ``execution/idempotency.py``).

    Riceve tutto cio' che distingue una decisione da un'altra: due decisioni
    diverse devono produrre id diversi, e la stessa decisione ricalcolata
    dopo un riavvio deve produrre lo stesso id.
    """

    def __call__(
        self,
        *,
        symbol: str,
        side: Side,
        quantity: Decimal,
        intent: str,
        ts_ms: TimestampMs,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Tutto cio' che il risk manager deve sapere sul mercato in questo istante.

    ``payoff_ratio`` arriva dal generatore di segnali: e' il rapporto fra la
    vincita attesa (distanza dal take profit) e la perdita attesa (distanza
    dallo stop). Senza di esso Kelly non e' calcolabile e il sizing rifiuta.

    ``expected_move_bps`` e' l'ampiezza della vincita attesa, in punti base.
    Serve per confrontare l'edge con i costi: una probabilita' da sola non si
    puo' confrontare con una commissione.
    """

    price: Decimal
    filters: SymbolFilters
    payoff_ratio: float
    #: Eta' del dato su cui si sta decidendo, in millisecondi. Non ha un
    #: default: ogni chiamante deve dichiararla, perche' "non lo so" e
    #: "e' fresca" non sono la stessa cosa e il secondo apre posizioni.
    data_age_ms: float
    expected_move_bps: float = 0.0
    available_quote: Decimal | None = None
    last_order_latency_ms: float | None = None
    estimated_cost_bps: float = 0.0


@dataclass
class RiskManager:
    """Orchestratore dei controlli di rischio."""

    config: AppConfig
    clock: Clock
    killswitch: KillSwitch
    sizer: PositionSizer
    limits: LimitChecker
    client_order_id_factory: ClientOrderIdFactory
    decisions: list[Decision] = field(default_factory=list)
    #: Decisioni conservate in memoria. A dieci valutazioni al secondo, una
    #: lista senza limite consuma la RAM nel giro di giorni: le righe piu'
    #: vecchie restano comunque nel log su disco.
    max_decisions_kept: int = 5_000

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        clock: Clock,
        client_order_id_factory: ClientOrderIdFactory,
    ) -> RiskManager:
        """Costruisce il risk manager dalla configurazione validata."""
        caps = {s.symbol: s.max_position_quote for s in config.enabled_symbols}
        return cls(
            config=config,
            clock=clock,
            killswitch=KillSwitch(config.risk.killswitch),
            sizer=PositionSizer(config.risk.sizing),
            limits=LimitChecker(
                config.risk.limits,
                max_position_quote=caps,
                correlations=CorrelationTracker(
                    config.risk.limits.correlation_lookback_bars,
                    config.risk.limits.min_correlation_samples,
                ),
                rates=OrderRateTracker(),
            ),
            client_order_id_factory=client_order_id_factory,
        )

    def trim_decisions(self) -> int:
        """Scarta le decisioni piu' vecchie oltre il limite. Restituisce quante."""
        eccesso = len(self.decisions) - self.max_decisions_kept
        if eccesso > 0:
            del self.decisions[:eccesso]
            return eccesso
        return 0

    # -- valutazione ------------------------------------------------------

    def evaluate(
        self, signal: Signal, snapshot: PortfolioSnapshot, context: MarketContext
    ) -> Decision:
        """Valuta un segnale e restituisce la decisione, sempre loggabile."""
        now_ms = self.clock.now_ms()
        reasons: list[str] = []

        events = self.killswitch.check(
            snapshot, now_ms=now_ms, last_order_latency_ms=context.last_order_latency_ms
        )
        reasons.extend(event.describe() for event in events)

        side, intent = self._classify(signal, snapshot)
        if intent is None:
            return self._reject(
                signal,
                snapshot,
                context,
                now_ms,
                [*reasons, "segnale senza effetto sulla posizione"],
            )

        if intent.adds_exposure and not self.killswitch.allows_opening(now_ms):
            last = self.killswitch.last_event
            reasons.append(
                "kill switch attivo, nessuna nuova esposizione"
                + (f": {last.describe()}" if last else "")
            )

        if intent.adds_exposure and context.filters.is_stale(
            now_ms, self.config.exchange.exchange_info_refresh_s * 1000
        ):
            reasons.append(
                f"filtri di {signal.symbol} non aggiornati "
                f"(letti a {context.filters.fetched_at_ms}): vanno riletti da exchangeInfo"
            )

        # Il vettore puo' essere completo (barre a sufficienza, ATR calcolato)
        # e vecchio di un giorno: il conteggio delle barre non dice quando
        # sono arrivate. Aprire su un prezzo di ieri e' peggio che non aprire.
        soglia_ms = self.config.risk.killswitch.max_data_staleness_ms
        if intent.adds_exposure and context.data_age_ms > soglia_ms:
            reasons.append(
                f"dati vecchi di {context.data_age_ms:.0f} ms su {signal.symbol} "
                f"(limite {soglia_ms} ms): non si apre su un prezzo che non "
                "sappiamo se e' ancora quello"
            )

        reasons.extend(self._check_signal_quality(signal, context, intent))

        sizing = self._size(signal, snapshot, context, intent)
        reasons.extend(sizing.reasons)

        if sizing.quantity > 0:
            proposal = OrderProposal(
                symbol=signal.symbol,
                side=side,
                quantity=sizing.quantity,
                reference_price=context.price,
                intent=intent,
            )
            reasons.extend(self.limits.check(proposal, snapshot, now_ms=now_ms))

        if reasons:
            return self._reject(
                signal, snapshot, context, now_ms, reasons, sizing_kelly=sizing.kelly_applied
            )

        order = Order(
            client_order_id=self.client_order_id_factory(
                symbol=signal.symbol,
                side=side,
                quantity=sizing.quantity,
                intent=intent.value,
                ts_ms=now_ms,
            ),
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=sizing.quantity,
            created_at_ms=now_ms,
            time_in_force=TimeInForce.IOC,
            reduce_only=not intent.adds_exposure,
            signal_ts_ms=signal.ts_ms,
            metadata={"intent": intent.value, "reference_price": str(context.price)},
        )
        self.limits.rates.record(signal.symbol, now_ms)

        decision = Decision(
            ts_ms=now_ms,
            symbol=signal.symbol,
            signal=signal,
            approved=True,
            order=order,
            risk_snapshot=self._risk_snapshot(snapshot, context, intent, sizing.kelly_applied),
            mode=self.config.mode.value,
        )
        self.decisions.append(decision)
        self.trim_decisions()
        return decision

    def flatten_all(self, snapshot: PortfolioSnapshot, prices: dict[str, Decimal],
                    filters: dict[str, SymbolFilters]) -> list[Order]:
        """Ordini di chiusura per tutte le posizioni aperte.

        Usato dal kill switch quando ``flatten_positions_on_trigger`` e' attivo.
        Non passa dai limiti: chiudere e' sempre permesso. Una posizione che
        l'exchange non consente di liquidare (residuo sotto MIN_NOTIONAL) viene
        saltata e segnalata nei log, non ritentata all'infinito.
        """
        now_ms = self.clock.now_ms()
        orders: list[Order] = []
        for symbol, position in snapshot.open_positions.items():
            symbol_filters = filters.get(symbol)
            price = prices.get(symbol)
            if symbol_filters is None or price is None:
                continue
            sizing = self.sizer.size_to_close(
                quantity_to_close=position.quantity, price=price, filters=symbol_filters
            )
            if not sizing.is_tradeable:
                self.decisions.append(
                    Decision(
                        ts_ms=now_ms,
                        symbol=symbol,
                        signal=None,
                        approved=False,
                        rejection_reasons=sizing.reasons,
                        risk_snapshot={"azione": "flatten", "posizione": str(position.quantity)},
                        mode=self.config.mode.value,
                    )
                )
                continue
            side = Side.SELL if position.quantity > 0 else Side.BUY
            orders.append(
                Order(
                    client_order_id=self.client_order_id_factory(
                        symbol=symbol,
                        side=side,
                        quantity=sizing.quantity,
                        intent=OrderIntent.CLOSE.value,
                        ts_ms=now_ms,
                    ),
                    symbol=symbol,
                    side=side,
                    order_type=OrderType.MARKET,
                    quantity=sizing.quantity,
                    created_at_ms=now_ms,
                    time_in_force=TimeInForce.IOC,
                    reduce_only=True,
                    metadata={"intent": OrderIntent.CLOSE.value, "origine": "killswitch"},
                )
            )
        return orders

    # -- interno ----------------------------------------------------------

    def _classify(
        self, signal: Signal, snapshot: PortfolioSnapshot
    ) -> tuple[Side, OrderIntent | None]:
        """Traduce la direzione del segnale in lato e intento, data la posizione.

        Sullo spot l'unico modo di esprimere una vista ribassista su un simbolo
        gia' in portafoglio e' venderlo: un segnale SHORT senza posizione non
        produce un ordine.
        """
        position = snapshot.position(signal.symbol)
        if signal.direction is SignalDirection.LONG:
            if position.quantity > 0:
                return Side.BUY, OrderIntent.INCREASE
            return Side.BUY, OrderIntent.OPEN
        if signal.direction is SignalDirection.SHORT:
            if position.quantity > 0:
                return Side.SELL, OrderIntent.CLOSE
            return Side.SELL, None
        if not position.is_flat:
            side = Side.SELL if position.quantity > 0 else Side.BUY
            return side, OrderIntent.CLOSE
        return Side.BUY, None

    def _check_signal_quality(
        self, signal: Signal, context: MarketContext, intent: OrderIntent
    ) -> list[str]:
        """Soglie di qualita' del segnale. Non si applicano alle chiusure."""
        if not intent.adds_exposure:
            return []
        reasons: list[str] = []
        settings = self.config.signals
        if signal.probability < settings.min_probability:
            reasons.append(
                f"probabilita' {signal.probability:.3f} sotto la soglia "
                f"{settings.min_probability:.3f}"
            )
        # Principio 5: l'edge si misura al netto dei costi, mai al lordo.
        # Valore atteso in bps = ampiezza della vincita x frazione di Kelly piena,
        # cioe' p * tp - (1 - p) * sl con sl = tp / payoff_ratio.
        gross_edge_bps = context.expected_move_bps * kelly_fraction(
            signal.probability, context.payoff_ratio
        )
        net_edge_bps = gross_edge_bps - context.estimated_cost_bps
        if net_edge_bps < settings.min_expected_edge_bps:
            reasons.append(
                f"edge netto {net_edge_bps:.1f} bps (lordo {gross_edge_bps:.1f}, "
                f"costi {context.estimated_cost_bps:.1f}) sotto la soglia "
                f"{settings.min_expected_edge_bps:.1f} bps"
            )
        return reasons

    def _size(
        self,
        signal: Signal,
        snapshot: PortfolioSnapshot,
        context: MarketContext,
        intent: OrderIntent,
    ) -> SizingResult:
        if intent.adds_exposure:
            return self.sizer.size(
                probability=signal.probability,
                payoff_ratio=context.payoff_ratio,
                price=context.price,
                equity=snapshot.equity,
                filters=context.filters,
                available_quote=context.available_quote,
            )
        return self.sizer.size_to_close(
            quantity_to_close=snapshot.position(signal.symbol).quantity,
            price=context.price,
            filters=context.filters,
        )

    def _reject(
        self,
        signal: Signal,
        snapshot: PortfolioSnapshot,
        context: MarketContext,
        now_ms: TimestampMs,
        reasons: list[str],
        *,
        sizing_kelly: float = 0.0,
    ) -> Decision:
        decision = Decision(
            ts_ms=now_ms,
            symbol=signal.symbol,
            signal=signal,
            approved=False,
            rejection_reasons=tuple(reasons),
            risk_snapshot=self._risk_snapshot(snapshot, context, None, sizing_kelly),
            mode=self.config.mode.value,
        )
        self.decisions.append(decision)
        self.trim_decisions()
        return decision

    def _risk_snapshot(
        self,
        snapshot: PortfolioSnapshot,
        context: MarketContext,
        intent: OrderIntent | None,
        kelly_applied: float,
    ) -> dict[str, object]:
        """Contesto numerico allegato a ogni decisione, per l'autopsia."""
        return {
            "equity": str(snapshot.equity),
            "cash": str(snapshot.cash),
            "gross_exposure": str(snapshot.gross_exposure),
            "open_positions": snapshot.open_position_count,
            "consecutive_losses": snapshot.consecutive_losses,
            "daily_drawdown_pct": round(snapshot.daily_drawdown_pct, 6),
            "total_drawdown_pct": round(snapshot.total_drawdown_pct, 6),
            "killswitch_tripped": self.killswitch.is_tripped,
            "intent": intent.value if intent else None,
            "kelly_applied": round(kelly_applied, 6),
            "reference_price": str(context.price),
            "estimated_cost_bps": context.estimated_cost_bps,
        }
