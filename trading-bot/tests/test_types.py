"""Test dei tipi di dominio: aritmetica della posizione e invarianti."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.types import (
    BookLevel,
    Candle,
    Decision,
    Fill,
    Order,
    OrderBookSnapshot,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Signal,
    SignalDirection,
    Trade,
    datetime_to_ms,
    ms_to_datetime,
)

D = Decimal


def make_fill(side: Side, price: str, qty: str, fee: str = "0", ts: int = 1) -> Fill:
    return Fill(
        client_order_id="c1",
        symbol="BTCUSDT",
        side=side,
        price=D(price),
        quantity=D(qty),
        fee=D(fee),
        fee_asset="USDT",
        ts_ms=ts,
        is_maker=True,
        trade_id=f"t{ts}",
    )


class TestPosition:
    def test_apertura(self) -> None:
        position, realized = Position("BTCUSDT").apply_fill(make_fill(Side.BUY, "100", "2"))
        assert position.quantity == D(2)
        assert position.average_entry_price == D(100)
        assert realized == 0

    def test_incremento_media_ponderata(self) -> None:
        position = Position("BTCUSDT")
        position, _ = position.apply_fill(make_fill(Side.BUY, "100", "1"))
        position, _ = position.apply_fill(make_fill(Side.BUY, "200", "1", ts=2))
        assert position.average_entry_price == D(150)

    def test_chiusura_parziale_realizza_proporzionalmente(self) -> None:
        position = Position("BTCUSDT")
        position, _ = position.apply_fill(make_fill(Side.BUY, "100", "4"))
        position, realized = position.apply_fill(make_fill(Side.SELL, "110", "1", ts=2))
        assert realized == D(10)
        assert position.quantity == D(3)
        assert position.average_entry_price == D(100)

    def test_short_in_profitto_quando_il_prezzo_scende(self) -> None:
        position, _ = Position("BTCUSDT").apply_fill(make_fill(Side.SELL, "100", "1"))
        assert position.quantity == D(-1)
        assert position.unrealized_pnl(D(90)) == D(10)
        position, realized = position.apply_fill(make_fill(Side.BUY, "90", "1", ts=2))
        assert realized == D(10)
        assert position.is_flat

    def test_inversione_di_segno(self) -> None:
        """Vendere piu' di quanto si possiede apre uno short al prezzo del fill."""
        position = Position("BTCUSDT")
        position, _ = position.apply_fill(make_fill(Side.BUY, "100", "2"))
        position, realized = position.apply_fill(make_fill(Side.SELL, "110", "3", ts=2))
        assert realized == D(20)
        assert position.quantity == D(-1)
        assert position.average_entry_price == D(110)

    def test_le_fee_sono_sempre_sottratte(self) -> None:
        """Principio 5: nessun risultato al lordo delle commissioni."""
        fill = make_fill(Side.BUY, "100", "1", fee="0.5")
        position, realized = Position("BTCUSDT").apply_fill(fill)
        assert realized == D("-0.5")
        assert position.fees_paid == D("0.5")
        assert position.realized_pnl == D("-0.5")

    def test_fill_di_un_altro_simbolo_rifiutato(self) -> None:
        fill = Fill("c", "ETHUSDT", Side.BUY, D(1), D(1), D(0), "USDT", 1, True, "t")
        with pytest.raises(ValueError, match="ETHUSDT"):
            Position("BTCUSDT").apply_fill(fill)

    def test_posizione_piatta(self) -> None:
        assert Position("BTCUSDT").is_flat
        assert Position("BTCUSDT").side is None
        assert Position("BTCUSDT").unrealized_pnl(D(100)) == 0


class TestCandle:
    def _candle(self, **overrides: object) -> Candle:
        kwargs: dict[str, object] = dict(
            symbol="BTCUSDT",
            open_time_ms=0,
            close_time_ms=60_000,
            open=D(100),
            high=D(110),
            low=D(90),
            close=D(105),
            volume=D(1),
            quote_volume=D(100),
            trades=10,
            is_closed=True,
        )
        kwargs.update(overrides)
        return Candle(**kwargs)  # type: ignore[arg-type]

    def test_candela_aperta_non_ha_istante_di_osservabilita(self) -> None:
        """Anti-look-ahead: una candela in formazione non e' ancora osservabile."""
        with pytest.raises(ValueError, match="non chiusa"):
            _ = self._candle(is_closed=False).decision_time_ms

    def test_candela_chiusa_osservabile_dalla_chiusura(self) -> None:
        assert self._candle().decision_time_ms == 60_000

    def test_tempi_incoerenti_rifiutati(self) -> None:
        with pytest.raises(ValueError):
            self._candle(close_time_ms=0)

    def test_low_sopra_high_rifiutato(self) -> None:
        with pytest.raises(ValueError):
            self._candle(low=D(200))


class TestOrderBook:
    def _book(self, bid_qty: str = "1", ask_qty: str = "1") -> OrderBookSnapshot:
        return OrderBookSnapshot(
            symbol="BTCUSDT",
            last_update_id=1,
            bids=(BookLevel(D(100), D(bid_qty)),),
            asks=(BookLevel(D(102), D(ask_qty)),),
            ts_ms=1,
        )

    def test_mid_e_spread(self) -> None:
        book = self._book()
        assert book.mid_price == D(101)
        assert book.spread == D(2)

    def test_microprice_pende_verso_il_lato_sottile(self) -> None:
        """Molta domanda al bid e poca offerta: il fair value sale verso l'ask."""
        book = self._book(bid_qty="9", ask_qty="1")
        mid = book.mid_price
        micro = book.microprice
        assert mid is not None and micro is not None
        assert micro > mid

    def test_book_vuoto_non_esplode(self) -> None:
        empty = OrderBookSnapshot("BTCUSDT", 1, (), (), 1)
        assert empty.mid_price is None
        assert empty.microprice is None
        assert not empty.is_crossed()

    def test_book_incrociato_rilevato(self) -> None:
        crossed = OrderBookSnapshot(
            "BTCUSDT", 1, (BookLevel(D(103), D(1)),), (BookLevel(D(102), D(1)),), 1
        )
        assert crossed.is_crossed()


class TestSignalAndOrder:
    def test_probabilita_fuori_range_rifiutata(self) -> None:
        with pytest.raises(ValueError, match="probability"):
            Signal("BTCUSDT", 1, SignalDirection.LONG, 1.5, {}, "rules", 1000)

    def test_edge_rispetto_al_coin_flip(self) -> None:
        signal = Signal("BTCUSDT", 1, SignalDirection.LONG, 0.6, {"ofi": 0.3}, "rules", 1000)
        assert signal.edge == pytest.approx(0.1)

    def test_ordine_limite_senza_prezzo_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="prezzo"):
            Order("c1", "BTCUSDT", Side.BUY, OrderType.LIMIT, D(1), 1)

    def test_quantita_non_positiva_rifiutata(self) -> None:
        with pytest.raises(ValueError, match="quantita"):
            Order("c1", "BTCUSDT", Side.BUY, OrderType.MARKET, D(0), 1)

    def test_filled_oltre_la_quantita_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="filled"):
            Order("c1", "BTCUSDT", Side.BUY, OrderType.MARKET, D(1), 1, filled_quantity=D(2))

    def test_stato_terminale(self) -> None:
        assert OrderStatus.FILLED.is_terminal
        assert not OrderStatus.FILLED.is_open
        assert OrderStatus.PARTIALLY_FILLED.is_open
        assert not OrderStatus.LOCAL.is_open

    def test_lato_aggressore_del_trade(self) -> None:
        assert Trade("BTCUSDT", 1, D(100), D(1), 1, is_buyer_maker=True).aggressor_side is Side.SELL
        assert Trade("BTCUSDT", 1, D(100), D(1), 1, is_buyer_maker=False).aggressor_side is Side.BUY


class TestDecision:
    def test_rifiuto_senza_motivazione_vietato(self) -> None:
        """Senza motivazione l'autopsia e' impossibile: principio 4."""
        with pytest.raises(ValueError, match="autopsia"):
            Decision(ts_ms=1, symbol="BTCUSDT", signal=None, approved=False)

    def test_approvazione_senza_ordine_vietata(self) -> None:
        with pytest.raises(ValueError, match="senza ordine"):
            Decision(ts_ms=1, symbol="BTCUSDT", signal=None, approved=True)

    def test_rifiuto_motivato_valido(self) -> None:
        decision = Decision(
            ts_ms=1,
            symbol="BTCUSDT",
            signal=None,
            approved=False,
            rejection_reasons=("notional sotto il minimo di exchange",),
        )
        assert not decision.approved


class TestTimeHelpers:
    def test_roundtrip(self) -> None:
        dt = datetime(2026, 1, 1, 12, 30, tzinfo=UTC)
        assert ms_to_datetime(datetime_to_ms(dt)) == dt

    def test_naive_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="naive"):
            datetime_to_ms(datetime(2026, 1, 1, 12, 30))  # noqa: DTZ001 - il naive e' il caso sotto test
