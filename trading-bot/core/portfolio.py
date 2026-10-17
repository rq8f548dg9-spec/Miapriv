"""Stato di portafoglio: posizioni, equity, esito dei trade.

Separato dal risk manager di proposito: il portafoglio registra cosa e'
successo, il risk manager decide cosa puo' succedere. Lo snapshot immutabile
che il portafoglio produce e' l'unico input di stato che i controlli di
rischio leggono, cosi' un controllo non puo' modificare lo stato che valuta.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from core.types import Fill, Position, TimestampMs

__all__ = ["Portfolio", "PortfolioSnapshot", "TradeResult"]


@dataclass(frozen=True, slots=True)
class TradeResult:
    """Esito di una chiusura (totale o parziale). Alimenta le perdite consecutive."""

    symbol: str
    ts_ms: TimestampMs
    realized_pnl: Decimal

    @property
    def is_loss(self) -> bool:
        return self.realized_pnl < 0


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Fotografia immutabile dello stato, passata ai controlli di rischio."""

    ts_ms: TimestampMs
    cash: Decimal
    positions: Mapping[str, Position]
    mark_prices: Mapping[str, Decimal]
    realized_pnl: Decimal
    fees_paid: Decimal
    consecutive_losses: int
    day_start_equity: Decimal
    peak_equity: Decimal

    @property
    def open_positions(self) -> Mapping[str, Position]:
        return {s: p for s, p in self.positions.items() if not p.is_flat}

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)

    @property
    def gross_exposure(self) -> Decimal:
        """Somma dei notional assoluti. Un simbolo senza prezzo di mercato
        non puo' essere valutato: si usa il prezzo di carico, che e' una
        sottostima prudente solo se il prezzo non si e' mosso contro."""
        total = Decimal(0)
        for symbol, position in self.open_positions.items():
            price = self.mark_prices.get(symbol, position.average_entry_price)
            total += position.notional(price)
        return total

    @property
    def unrealized_pnl(self) -> Decimal:
        total = Decimal(0)
        for symbol, position in self.open_positions.items():
            price = self.mark_prices.get(symbol)
            if price is not None:
                total += position.unrealized_pnl(price)
        return total

    @property
    def positions_value(self) -> Decimal:
        """Valore di mercato firmato delle posizioni.

        Un simbolo senza prezzo corrente viene valutato al prezzo di carico:
        e' l'ultimo valore noto, non una stima inventata.
        """
        total = Decimal(0)
        for symbol, position in self.open_positions.items():
            price = self.mark_prices.get(symbol, position.average_entry_price)
            total += position.quantity * price
        return total

    @property
    def equity(self) -> Decimal:
        """Cash piu' valore di mercato delle posizioni.

        Comprare non riduce l'equity: sposta cash in posizione. Solo il
        movimento del prezzo e le fee la cambiano.
        """
        return self.cash + self.positions_value

    @property
    def daily_drawdown_pct(self) -> float:
        """Perdita dall'equity di inizio giornata, come frazione positiva."""
        if self.day_start_equity <= 0:
            return 0.0
        delta = self.equity - self.day_start_equity
        return float(-delta / self.day_start_equity) if delta < 0 else 0.0

    @property
    def total_drawdown_pct(self) -> float:
        """Perdita dal massimo storico dell'equity (high-water mark)."""
        if self.peak_equity <= 0:
            return 0.0
        delta = self.equity - self.peak_equity
        return float(-delta / self.peak_equity) if delta < 0 else 0.0

    def position(self, symbol: str) -> Position:
        return self.positions.get(symbol, Position(symbol))

    def exposure_for(self, symbol: str) -> Decimal:
        position = self.position(symbol)
        if position.is_flat:
            return Decimal(0)
        price = self.mark_prices.get(symbol, position.average_entry_price)
        return position.notional(price)


@dataclass
class Portfolio:
    """Contabilita' del bot. Unica sorgente dello stato locale.

    Non e' la verita' assoluta: all'avvio e periodicamente va riconciliata con
    l'exchange (``execution/reconcile.py``). La verita' sta sull'exchange.
    """

    initial_cash: Decimal
    cash: Decimal = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    mark_prices: dict[str, Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal(0)
    fees_paid: Decimal = Decimal(0)
    trades: list[TradeResult] = field(default_factory=list)
    peak_equity: Decimal = field(init=False)
    day_start_equity: Decimal = field(init=False)
    _day_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError(f"capitale iniziale {self.initial_cash} non positivo")
        self.cash = self.initial_cash
        self.peak_equity = self.initial_cash
        self.day_start_equity = self.initial_cash

    def apply_fill(self, fill: Fill) -> TradeResult | None:
        """Registra un fill. Restituisce l'esito solo se ha chiuso esposizione.

        Il cash si muove per il controvalore scambiato piu' le fee: comprare
        consuma cash, vendere lo restituisce. Cosi' l'equity resta confrontabile
        con il saldo reale dell'exchange.
        """
        position = self.positions.get(fill.symbol, Position(fill.symbol))
        was_open = not position.is_flat
        reduces = was_open and (position.quantity > 0) != (fill.signed_quantity > 0)

        new_position, realized = position.apply_fill(fill)
        self.positions[fill.symbol] = new_position

        self.cash -= fill.signed_quantity * fill.price + fill.fee
        self.fees_paid += fill.fee
        self.realized_pnl += realized
        self.mark_prices[fill.symbol] = fill.price
        self._update_peak()

        if not reduces:
            return None
        result = TradeResult(symbol=fill.symbol, ts_ms=fill.ts_ms, realized_pnl=realized)
        self.trades.append(result)
        return result

    def update_mark(self, symbol: str, price: Decimal) -> None:
        if price <= 0:
            raise ValueError(f"prezzo di mercato {price} non positivo per {symbol}")
        self.mark_prices[symbol] = price
        self._update_peak()

    def start_new_day(self, day_key: str) -> bool:
        """Azzera il riferimento del drawdown giornaliero. Vero se il giorno e' cambiato.

        La chiave del giorno arriva dall'esterno (dal ``Clock``): il portafoglio
        non legge l'orologio di sistema, altrimenti backtest e live divergono.
        """
        if self._day_key == day_key:
            return False
        self._day_key = day_key
        self.day_start_equity = self.equity
        return True

    @property
    def consecutive_losses(self) -> int:
        count = 0
        for trade in reversed(self.trades):
            if not trade.is_loss:
                break
            count += 1
        return count

    @property
    def unrealized_pnl(self) -> Decimal:
        total = Decimal(0)
        for symbol, position in self.positions.items():
            price = self.mark_prices.get(symbol)
            if price is not None and not position.is_flat:
                total += position.unrealized_pnl(price)
        return total

    @property
    def positions_value(self) -> Decimal:
        total = Decimal(0)
        for symbol, position in self.positions.items():
            if position.is_flat:
                continue
            price = self.mark_prices.get(symbol, position.average_entry_price)
            total += position.quantity * price
        return total

    @property
    def equity(self) -> Decimal:
        """Cash piu' valore di mercato delle posizioni: comprare non la riduce."""
        return self.cash + self.positions_value

    def snapshot(self, ts_ms: TimestampMs) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            ts_ms=ts_ms,
            cash=self.cash,
            positions=dict(self.positions),
            mark_prices=dict(self.mark_prices),
            realized_pnl=self.realized_pnl,
            fees_paid=self.fees_paid,
            consecutive_losses=self.consecutive_losses,
            day_start_equity=self.day_start_equity,
            peak_equity=self.peak_equity,
        )

    def _update_peak(self) -> None:
        equity = self.equity
        if equity > self.peak_equity:
            self.peak_equity = equity
