"""Tipi di dominio condivisi da backtest, testnet e live.

Regole di progetto applicate qui:

* Tutti i prezzi e le quantita' sono ``Decimal``. I float introducono errori di
  arrotondamento che un exchange rifiuta (LOT_SIZE / PRICE_FILTER) e che in un
  backtest si accumulano silenziosamente.
* Tutti gli istanti sono interi in millisecondi epoch UTC (``TimestampMs``).
  Nessun ``datetime`` naive circola nel sistema.
* Le strutture sono immutabili (``frozen=True``): lo stato evolve creando nuovi
  oggetti, cosi' un record loggato non puo' essere mutato a posteriori.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

__all__ = [
    "BookLevel",
    "Candle",
    "Decision",
    "Fill",
    "Order",
    "OrderBookSnapshot",
    "OrderStatus",
    "OrderType",
    "Position",
    "Side",
    "Signal",
    "SignalDirection",
    "TimeInForce",
    "TimestampMs",
    "Trade",
    "datetime_to_ms",
    "ms_to_datetime",
]

TimestampMs = int
"""Millisecondi dall'epoch, UTC. Unita' di tempo unica del sistema."""


def ms_to_datetime(ts_ms: TimestampMs) -> datetime:
    """Converte un timestamp in ms epoch in un ``datetime`` aware UTC."""
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)


def datetime_to_ms(dt: datetime) -> TimestampMs:
    """Converte un ``datetime`` aware in ms epoch. Rifiuta i naive."""
    if dt.tzinfo is None:
        raise ValueError("datetime naive non ammesso: serve un tzinfo esplicito")
    return int(dt.timestamp() * 1000)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 per BUY, -1 per SELL. Utile per la quantita' firmata."""
        return 1 if self is Side.BUY else -1

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class OrderStatus(StrEnum):
    """Stati di un ordine. ``LOCAL`` = creato ma non ancora accettato."""

    LOCAL = "LOCAL"
    PENDING_NEW = "PENDING_NEW"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def is_open(self) -> bool:
        """Vero se l'ordine puo' ancora consumare margine sull'exchange."""
        return not self.is_terminal and self is not OrderStatus.LOCAL


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        if self is SignalDirection.LONG:
            return 1
        if self is SignalDirection.SHORT:
            return -1
        return 0


@dataclass(frozen=True, slots=True)
class Candle:
    """Barra OHLCV.

    ``is_closed`` e' parte del contratto anti-look-ahead: una candela ancora
    aperta contiene informazione del futuro rispetto al suo ``open_time_ms`` e
    non puo' essere usata per decidere. Il feature engine lo verifica.
    """

    symbol: str
    open_time_ms: TimestampMs
    close_time_ms: TimestampMs
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int
    is_closed: bool

    def __post_init__(self) -> None:
        if self.close_time_ms <= self.open_time_ms:
            raise ValueError(
                f"candela {self.symbol}: close_time {self.close_time_ms} "
                f"non successivo a open_time {self.open_time_ms}"
            )
        if self.low > self.high:
            raise ValueError(f"candela {self.symbol}: low {self.low} > high {self.high}")

    @property
    def decision_time_ms(self) -> TimestampMs:
        """Primo istante in cui questa candela e' lecitamente osservabile.

        Per una candela chiusa coincide con la sua chiusura: prima di allora il
        suo contenuto non esiste ancora.
        """
        if not self.is_closed:
            raise ValueError(
                f"candela {self.symbol} @{self.open_time_ms} non chiusa: "
                "non ha un istante di osservabilita'"
            )
        return self.close_time_ms

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / Decimal(3)


@dataclass(frozen=True, slots=True)
class Trade:
    """Singola esecuzione pubblica dal feed aggTrade/trade."""

    symbol: str
    trade_id: int
    price: Decimal
    quantity: Decimal
    ts_ms: TimestampMs
    is_buyer_maker: bool

    @property
    def aggressor_side(self) -> Side:
        """Lato che ha consumato liquidita'. Base dell'order flow imbalance."""
        return Side.SELL if self.is_buyer_maker else Side.BUY

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    quantity: Decimal

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Book L2 a un istante. ``bids`` decrescente, ``asks`` crescente."""

    symbol: str
    last_update_id: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    ts_ms: TimestampMs

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return (bid.price + ask.price) / Decimal(2)

    @property
    def spread(self) -> Decimal | None:
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        return ask.price - bid.price

    @property
    def microprice(self) -> Decimal | None:
        """Mid pesato per il volume al top: stima migliore del fair value."""
        bid, ask = self.best_bid, self.best_ask
        if bid is None or ask is None:
            return None
        total = bid.quantity + ask.quantity
        if total == 0:
            return self.mid_price
        return (ask.price * bid.quantity + bid.price * ask.quantity) / total

    def is_crossed(self) -> bool:
        bid, ask = self.best_bid, self.best_ask
        return bid is not None and ask is not None and bid.price >= ask.price


@dataclass(frozen=True, slots=True)
class Signal:
    """Output di un ``SignalGenerator``.

    ``probability`` e' la probabilita' CALIBRATA che la direzione indicata
    risulti profittevole al netto dei costi sull'orizzonte ``horizon_ms``.
    ``features`` e' il dizionario che ha generato la decisione: viene loggato
    integralmente, senza di esso l'autopsia e' impossibile.
    """

    symbol: str
    ts_ms: TimestampMs
    direction: SignalDirection
    probability: float
    features: Mapping[str, float]
    generator: str
    horizon_ms: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError(f"probability {self.probability} fuori da [0, 1]")
        if self.horizon_ms <= 0:
            raise ValueError(f"horizon_ms {self.horizon_ms} deve essere positivo")

    @property
    def edge(self) -> float:
        """Scostamento dal coin flip. Negativo = segnale contro la direzione."""
        return self.probability - 0.5


@dataclass(frozen=True, slots=True)
class Order:
    """Intenzione di negoziare, gia' approvata dal risk manager.

    ``client_order_id`` e' deterministico (cfr. ``execution/idempotency.py``):
    reinviare lo stesso ordine dopo un crash non produce un secondo fill.
    """

    client_order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    created_at_ms: TimestampMs
    price: Decimal | None = None
    time_in_force: TimeInForce | None = None
    reduce_only: bool = False
    status: OrderStatus = OrderStatus.LOCAL
    exchange_order_id: str | None = None
    filled_quantity: Decimal = Decimal(0)
    average_fill_price: Decimal | None = None
    signal_ts_ms: TimestampMs | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(
                f"ordine {self.client_order_id}: quantita' {self.quantity} non positiva"
            )
        if self.order_type is not OrderType.MARKET and self.price is None:
            raise ValueError(f"ordine {self.client_order_id}: {self.order_type} richiede un prezzo")
        if self.price is not None and self.price <= 0:
            raise ValueError(f"ordine {self.client_order_id}: prezzo {self.price} non positivo")
        if self.filled_quantity < 0 or self.filled_quantity > self.quantity:
            raise ValueError(
                f"ordine {self.client_order_id}: filled {self.filled_quantity} "
                f"fuori da [0, {self.quantity}]"
            )

    @property
    def remaining_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    @property
    def notional(self) -> Decimal | None:
        """Notional stimato. ``None`` per i market order senza prezzo di riferimento."""
        if self.price is None:
            return None
        return self.price * self.quantity


@dataclass(frozen=True, slots=True)
class Fill:
    """Esecuzione (anche parziale) di un nostro ordine."""

    client_order_id: str
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    fee: Decimal
    fee_asset: str
    ts_ms: TimestampMs
    is_maker: bool
    trade_id: str
    exchange_order_id: str | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"fill {self.trade_id}: quantita' {self.quantity} non positiva")
        if self.price <= 0:
            raise ValueError(f"fill {self.trade_id}: prezzo {self.price} non positivo")

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity * self.side.sign


@dataclass(frozen=True, slots=True)
class Position:
    """Posizione netta su un simbolo. ``quantity`` firmata: <0 = short."""

    symbol: str
    quantity: Decimal = Decimal(0)
    average_entry_price: Decimal = Decimal(0)
    realized_pnl: Decimal = Decimal(0)
    fees_paid: Decimal = Decimal(0)
    opened_at_ms: TimestampMs | None = None
    last_update_ms: TimestampMs | None = None

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def side(self) -> Side | None:
        if self.quantity > 0:
            return Side.BUY
        if self.quantity < 0:
            return Side.SELL
        return None

    def notional(self, mark_price: Decimal) -> Decimal:
        """Esposizione lorda al prezzo di mercato corrente."""
        return abs(self.quantity) * mark_price

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        if self.is_flat:
            return Decimal(0)
        return (mark_price - self.average_entry_price) * self.quantity

    def apply_fill(self, fill: Fill) -> tuple[Position, Decimal]:
        """Applica un fill e restituisce (nuova posizione, pnl realizzato dal fill).

        Gestisce apertura, incremento, riduzione parziale e inversione di segno.
        Le fee sono sempre sottratte: nessun risultato "al lordo".
        """
        if fill.symbol != self.symbol:
            raise ValueError(f"fill su {fill.symbol} applicato a posizione {self.symbol}")

        delta = fill.signed_quantity
        new_quantity = self.quantity + delta
        realized = Decimal(0)
        new_entry = self.average_entry_price

        if self.quantity == 0 or (self.quantity > 0) == (delta > 0):
            # apertura o incremento: media ponderata del prezzo di carico
            total_cost = (
                self.average_entry_price * abs(self.quantity) + fill.price * fill.quantity
            )
            new_entry = total_cost / abs(new_quantity) if new_quantity != 0 else Decimal(0)
        else:
            closed = min(abs(delta), abs(self.quantity))
            # il segno della posizione precedente determina il verso del profitto
            realized = (fill.price - self.average_entry_price) * closed * (
                Decimal(1) if self.quantity > 0 else Decimal(-1)
            )
            if new_quantity == 0:
                new_entry = Decimal(0)
            elif (new_quantity > 0) != (self.quantity > 0):
                # inversione: il residuo apre una nuova posizione al prezzo del fill
                new_entry = fill.price

        return (
            Position(
                symbol=self.symbol,
                quantity=new_quantity,
                average_entry_price=new_entry,
                realized_pnl=self.realized_pnl + realized - fill.fee,
                fees_paid=self.fees_paid + fill.fee,
                opened_at_ms=fill.ts_ms if self.is_flat else self.opened_at_ms,
                last_update_ms=fill.ts_ms,
            ),
            realized - fill.fee,
        )


@dataclass(frozen=True, slots=True)
class Decision:
    """Riga di autopsia: una per ogni valutazione del bot, approvata o no.

    Principio 4 del progetto. Viene serializzata integralmente dal logger: se
    manca una di queste informazioni, a posteriori non si capisce piu' perche'
    il bot abbia fatto quello che ha fatto.
    """

    ts_ms: TimestampMs
    symbol: str
    signal: Signal | None
    approved: bool
    order: Order | None = None
    rejection_reasons: tuple[str, ...] = ()
    risk_snapshot: Mapping[str, Any] = field(default_factory=dict)
    mode: str = "unknown"

    def __post_init__(self) -> None:
        if self.approved and self.order is None:
            raise ValueError("decisione approvata senza ordine associato")
        if not self.approved and not self.rejection_reasons:
            raise ValueError("decisione rifiutata senza motivazione: autopsia impossibile")
