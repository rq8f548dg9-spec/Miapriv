"""Interfaccia astratta del broker e implementazione di carta.

L'astrazione esiste perche' backtest, testnet e live siano implementazioni
intercambiabili della stessa interfaccia. Il resto del sistema non deve
sapere dove sta andando l'ordine: lo decide la configurazione, in un punto
solo.

``PaperBroker`` serve a due cose: provare la logica di esecuzione senza
toccare la rete, e girare in modalita' ``backtest`` con lo stesso codice del
live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from decimal import Decimal

from backtest.costs import CostModel
from core.clock import Clock
from core.types import (
    Fill,
    Order,
    OrderStatus,
    Side,
    SymbolFilters,
    TimestampMs,
)

__all__ = [
    "AccountState",
    "Broker",
    "BrokerError",
    "DepthSnapshot",
    "OrderAck",
    "PaperBroker",
]

#: Snapshot REST del book: (lastUpdateId, livelli bid, livelli ask).
DepthSnapshot = tuple[
    int, tuple[tuple[Decimal, Decimal], ...], tuple[tuple[Decimal, Decimal], ...]
]


class BrokerError(RuntimeError):
    """Errore nell'interazione con l'exchange."""


@dataclass(frozen=True, slots=True)
class OrderAck:
    """Risposta dell'exchange a un invio.

    ``latency_ms`` non e' un dettaglio: il kill switch ci legge sopra, perche'
    un exchange che risponde in due secondi e' un exchange su cui non si puo'
    gestire il rischio.
    """

    client_order_id: str
    exchange_order_id: str | None
    status: OrderStatus
    filled_quantity: Decimal
    average_price: Decimal | None
    fills: tuple[Fill, ...]
    latency_ms: float
    raw: dict[str, object] = field(default_factory=dict)

    @property
    def is_accepted(self) -> bool:
        return self.status not in (OrderStatus.REJECTED, OrderStatus.EXPIRED)

    @property
    def is_partial(self) -> bool:
        return self.status is OrderStatus.PARTIALLY_FILLED


@dataclass(frozen=True, slots=True)
class AccountState:
    """Verita' dell'exchange: saldi e ordini aperti.

    Al riavvio lo stato si ricostruisce da qui, non dal file locale. Il file
    locale dice cosa il bot credeva; questo dice cosa e' successo davvero.
    """

    balances: dict[str, Decimal]
    open_orders: tuple[Order, ...]
    fetched_at_ms: TimestampMs

    def free(self, asset: str) -> Decimal:
        return self.balances.get(asset.upper(), Decimal(0))


class Broker(ABC):
    """Contratto che testnet, live e carta devono rispettare."""

    @abstractmethod
    async def exchange_filters(self) -> dict[str, SymbolFilters]:
        """Filtri correnti da ``exchangeInfo``. Mai valori ipotizzati."""

    @abstractmethod
    async def account_state(self) -> AccountState:
        """Saldi e ordini aperti, come li vede l'exchange."""

    @abstractmethod
    async def submit(self, order: Order) -> OrderAck:
        """Invia un ordine. L'idempotenza e' garantita dal client order id."""

    @abstractmethod
    async def cancel(self, symbol: str, client_order_id: str) -> bool:
        """Annulla un ordine. ``False`` se non esisteva piu'."""

    @abstractmethod
    async def order_status(self, symbol: str, client_order_id: str) -> OrderAck | None:
        """Stato di un ordine inviato, o ``None`` se l'exchange non lo conosce.

        E' la domanda che si fa al riavvio per ogni ordine rimasto in volo.
        """

    @abstractmethod
    async def recent_fills(self, symbol: str, since_ms: TimestampMs) -> list[Fill]:
        """Esecuzioni recenti sul simbolo, per ricostruire cosa e' accaduto."""

    async def depth_snapshot(
        self, symbol: str, limit: int = 100
    ) -> DepthSnapshot | None:
        """Snapshot REST del book: (lastUpdateId, bid, ask).

        Serve a riallineare il book dopo un buco di sequenza. ``None`` per le
        implementazioni che non hanno un book da ricostruire.
        """
        return None

    async def close(self) -> None:
        """Rilascia le risorse. Le implementazioni di rete chiudono la sessione."""
        return None


@dataclass
class PaperBroker(Broker):
    """Broker simulato: esegue subito al prezzo corrente, con costi applicati.

    Non e' un giocattolo: applica gli stessi filtri e gli stessi costi del
    motore di backtest, e rispetta l'idempotenza. Serve a provare l'intera
    catena di esecuzione senza rischiare nulla.
    """

    clock: Clock
    costs: CostModel
    filters: dict[str, SymbolFilters]
    balances: dict[str, Decimal] = field(default_factory=dict)
    prices: dict[str, Decimal] = field(default_factory=dict)
    quote_asset: str = "USDT"
    simulated_latency_ms: float = 50.0
    #: Ordini gia' eseguiti, per id: un reinvio restituisce la stessa risposta.
    acks: dict[str, OrderAck] = field(default_factory=dict)
    fills: list[Fill] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)

    def set_price(self, symbol: str, price: Decimal) -> None:
        self.prices[symbol] = price

    async def exchange_filters(self) -> dict[str, SymbolFilters]:
        """Filtri con l'istante di lettura aggiornato.

        Una vera chiamata a exchangeInfo restituisce dati freschi: il broker
        di carta deve comportarsi allo stesso modo, altrimenti i filtri
        risultano scaduti per sempre.
        """
        now_ms = self.clock.now_ms()
        return {
            symbol: replace(filters, fetched_at_ms=now_ms)
            for symbol, filters in self.filters.items()
        }

    async def account_state(self) -> AccountState:
        return AccountState(
            balances=dict(self.balances),
            open_orders=(),  # il broker di carta esegue subito: niente resta aperto
            fetched_at_ms=self.clock.now_ms(),
        )

    async def submit(self, order: Order) -> OrderAck:
        """Esegue immediatamente. Un id gia' visto restituisce la vecchia risposta."""
        existing = self.acks.get(order.client_order_id)
        if existing is not None:
            # Idempotenza: il reinvio non produce un secondo fill.
            return existing

        price = self.prices.get(order.symbol)
        if price is None:
            raise BrokerError(f"nessun prezzo disponibile per {order.symbol}")

        symbol_filters = self.filters.get(order.symbol)
        if symbol_filters is not None:
            violations = symbol_filters.quantity_violations(order.quantity, price)
            if violations:
                ack = OrderAck(
                    client_order_id=order.client_order_id,
                    exchange_order_id=None,
                    status=OrderStatus.REJECTED,
                    filled_quantity=Decimal(0),
                    average_price=None,
                    fills=(),
                    latency_ms=self.simulated_latency_ms,
                    raw={"motivo": "; ".join(violations)},
                )
                self.acks[order.client_order_id] = ack
                self.rejected.append((order.client_order_id, "; ".join(violations)))
                return ack

        cost = self.costs.apply(
            side=order.side, reference_price=price, quantity=order.quantity
        )
        now_ms = self.clock.now_ms()
        fill = Fill(
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            price=cost.fill_price,
            quantity=order.quantity,
            fee=cost.fee,
            fee_asset=self.quote_asset,
            ts_ms=now_ms,
            is_maker=cost.is_maker,
            trade_id=f"paper-{len(self.fills) + 1}",
            exchange_order_id=f"paper-{order.client_order_id}",
        )
        self.fills.append(fill)
        self._apply_to_balances(fill)

        ack = OrderAck(
            client_order_id=order.client_order_id,
            exchange_order_id=fill.exchange_order_id,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            average_price=cost.fill_price,
            fills=(fill,),
            latency_ms=self.simulated_latency_ms,
        )
        self.acks[order.client_order_id] = ack
        return ack

    async def cancel(self, symbol: str, client_order_id: str) -> bool:
        return False  # niente resta aperto: non c'e' nulla da annullare

    async def order_status(self, symbol: str, client_order_id: str) -> OrderAck | None:
        return self.acks.get(client_order_id)

    async def recent_fills(self, symbol: str, since_ms: TimestampMs) -> list[Fill]:
        return [f for f in self.fills if f.symbol == symbol and f.ts_ms >= since_ms]

    def _apply_to_balances(self, fill: Fill) -> None:
        base = fill.symbol.removesuffix(self.quote_asset)
        quote_delta = -fill.signed_quantity * fill.price - fill.fee
        self.balances[base] = self.balances.get(base, Decimal(0)) + fill.signed_quantity
        self.balances[self.quote_asset] = (
            self.balances.get(self.quote_asset, Decimal(0)) + quote_delta
        )


def side_of(quantity: Decimal) -> Side:
    """Lato necessario per annullare una posizione di quantita' data."""
    return Side.SELL if quantity > 0 else Side.BUY
