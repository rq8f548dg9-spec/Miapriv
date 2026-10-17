"""Riconciliazione fra stato locale e stato dell'exchange.

Regola di fondo: la verita' sta sull'exchange. Il file di stato locale dice
cosa il bot credeva al momento dell'ultimo salvataggio, e fra quel momento e
adesso puo' essere successo di tutto: un fill parziale, un ordine eseguito
mentre il processo era morto, un intervento manuale.

La riconciliazione gira in due momenti:

* **all'avvio**, e lo stato locale viene ricostruito da zero dai saldi reali;
* **periodicamente**, e una divergenza oltre la tolleranza fa scattare il
  kill switch, perche' operare su una posizione di dimensione ignota e'
  peggio che non operare.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from core.portfolio import Portfolio
from core.types import Fill, OrderStatus, Position, TimestampMs
from execution.broker import Broker
from execution.idempotency import OrderRegistry
from risk.killswitch import KillSwitch

__all__ = ["PositionDiff", "Reconciler", "Reconciliation"]

logger = logging.getLogger("trading_bot.reconcile")


@dataclass(frozen=True, slots=True)
class PositionDiff:
    """Divergenza su un simbolo fra quantita' locale e quantita' reale."""

    symbol: str
    local_quantity: Decimal
    exchange_quantity: Decimal

    @property
    def delta(self) -> Decimal:
        return self.exchange_quantity - self.local_quantity

    @property
    def relative_error(self) -> float:
        """Errore relativo rispetto alla quantita' piu' grande fra le due."""
        reference = max(abs(self.local_quantity), abs(self.exchange_quantity))
        if reference == 0:
            return 0.0
        return float(abs(self.delta) / reference)

    def describe(self) -> str:
        return (
            f"{self.symbol}: locale {self.local_quantity}, exchange "
            f"{self.exchange_quantity} (scarto {self.delta})"
        )


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """Esito di un confronto."""

    ts_ms: TimestampMs
    differences: tuple[PositionDiff, ...]
    unknown_open_orders: tuple[str, ...]
    resolved_orders: tuple[str, ...]
    applied_fills: tuple[Fill, ...]

    @property
    def is_consistent(self) -> bool:
        return not self.differences and not self.unknown_open_orders

    def describe(self) -> str:
        if self.is_consistent:
            return "stato locale allineato con l'exchange"
        parts = [diff.describe() for diff in self.differences]
        if self.unknown_open_orders:
            parts.append(f"ordini aperti sconosciuti: {list(self.unknown_open_orders)}")
        return "; ".join(parts)


@dataclass
class Reconciler:
    """Confronta e, quando serve, ricostruisce lo stato locale."""

    broker: Broker
    registry: OrderRegistry
    killswitch: KillSwitch | None = None
    quote_asset: str = "USDT"
    #: Scarto relativo oltre il quale la divergenza non e' piu' arrotondamento.
    tolerance: float = 0.001
    #: Simboli di interesse: i saldi di altri asset non riguardano il bot.
    symbols: tuple[str, ...] = ()
    history: list[Reconciliation] = field(default_factory=list)

    def base_asset(self, symbol: str) -> str:
        return symbol.removesuffix(self.quote_asset)

    async def rebuild_portfolio(self, *, prices: dict[str, Decimal]) -> Portfolio:
        """Costruisce il portafoglio dai saldi reali. Usato all'avvio.

        Il prezzo medio di carico non e' deducibile dai soli saldi: si usa il
        prezzo corrente, dichiarando cosi' un PnL non realizzato pari a zero.
        E' una scelta prudente e dichiarata: inventare un prezzo di carico
        produrrebbe un PnL falso, e su quello si deciderebbe davvero.
        """
        state = await self.broker.account_state()
        cash = state.free(self.quote_asset)
        if cash <= 0:
            raise ValueError(
                f"saldo {self.quote_asset} nullo sull'exchange: "
                "il bot non puo' partire senza capitale"
            )

        portfolio = Portfolio(cash)
        for symbol in self.symbols:
            quantity = state.free(self.base_asset(symbol))
            if quantity <= 0:
                continue
            price = prices.get(symbol)
            if price is None:
                raise ValueError(
                    f"posizione su {symbol} ({quantity}) senza prezzo corrente: "
                    "impossibile valutarla, il bot non parte"
                )
            portfolio.positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                average_entry_price=price,
                opened_at_ms=state.fetched_at_ms,
                last_update_ms=state.fetched_at_ms,
            )
            portfolio.mark_prices[symbol] = price
            # Il controvalore delle posizioni e' gia' fuori dal cash.
        logger.info(
            "stato ricostruito dall'exchange: cash %s, posizioni %s",
            cash,
            {s: str(p.quantity) for s, p in portfolio.positions.items()},
        )
        return portfolio

    async def resolve_pending_orders(self) -> tuple[list[str], list[Fill]]:
        """Chiede all'exchange il destino degli ordini rimasti in volo.

        E' la domanda che risolve il caso peggiore: ordine inviato, risposta
        mai arrivata, processo riavviato. L'id deterministico fa si' che
        l'exchange sappia di quale ordine si sta parlando.
        """
        resolved: list[str] = []
        fills: list[Fill] = []
        for client_order_id in self.registry.pending_ids():
            symbol = self.registry.symbol_of(client_order_id)
            if symbol is None:
                continue
            ack = await self.broker.order_status(symbol, client_order_id)
            if ack is None:
                # L'exchange non lo conosce: non e' mai arrivato.
                self.registry.update(client_order_id, status=OrderStatus.REJECTED)
                resolved.append(client_order_id)
                logger.warning(
                    "ordine %s sconosciuto all'exchange: mai arrivato, marcato rifiutato",
                    client_order_id,
                )
                continue
            self.registry.update(
                client_order_id,
                status=ack.status,
                exchange_order_id=ack.exchange_order_id,
                filled_quantity=ack.filled_quantity,
            )
            if ack.status.is_terminal:
                resolved.append(client_order_id)
            fills.extend(ack.fills)
        return resolved, fills

    async def reconcile(
        self, portfolio: Portfolio, *, now_ms: TimestampMs
    ) -> Reconciliation:
        """Confronta lo stato locale con quello dell'exchange.

        Una divergenza oltre la tolleranza fa scattare il kill switch: da
        quel momento il bot puo' solo chiudere, non aprire.
        """
        resolved, fills = await self.resolve_pending_orders()
        state = await self.broker.account_state()

        differences: list[PositionDiff] = []
        for symbol in self.symbols:
            local = portfolio.positions.get(symbol, Position(symbol)).quantity
            remote = state.free(self.base_asset(symbol))
            diff = PositionDiff(symbol, local, remote)
            if diff.relative_error > self.tolerance:
                differences.append(diff)

        known_ids = self.registry.known_ids
        unknown = tuple(
            order.client_order_id
            for order in state.open_orders
            if order.client_order_id not in known_ids
        )

        result = Reconciliation(
            ts_ms=now_ms,
            differences=tuple(differences),
            unknown_open_orders=unknown,
            resolved_orders=tuple(resolved),
            applied_fills=tuple(fills),
        )
        self.history.append(result)

        if not result.is_consistent:
            logger.error("riconciliazione fallita: %s", result.describe())
            if self.killswitch is not None:
                self.killswitch.trip_on_state_mismatch(result.describe(), now_ms)
        else:
            logger.info("riconciliazione ok (%d ordini risolti)", len(resolved))

        return result

    def adopt_exchange_state(
        self, portfolio: Portfolio, reconciliation: Reconciliation, *, prices: dict[str, Decimal]
    ) -> None:
        """Allinea il portafoglio locale ai numeri dell'exchange.

        Si usa solo dopo un intervento consapevole: la riconciliazione da
        sola non riallinea in silenzio, perche' una divergenza e' un sintomo
        e cancellarla fa perdere l'informazione che l'ha causata.
        """
        for diff in reconciliation.differences:
            price = prices.get(diff.symbol)
            existing = portfolio.positions.get(diff.symbol, Position(diff.symbol))
            entry = existing.average_entry_price if existing.quantity > 0 else (price or Decimal(0))
            portfolio.positions[diff.symbol] = Position(
                symbol=diff.symbol,
                quantity=diff.exchange_quantity,
                average_entry_price=entry if entry > 0 else (price or Decimal(0)),
                realized_pnl=existing.realized_pnl,
                fees_paid=existing.fees_paid,
                opened_at_ms=existing.opened_at_ms,
                last_update_ms=reconciliation.ts_ms,
            )
            if price is not None:
                portfolio.mark_prices[diff.symbol] = price
            logger.warning("posizione %s allineata all'exchange: %s", diff.symbol, diff.describe())
