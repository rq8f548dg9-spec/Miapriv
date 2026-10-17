"""Test dell'idempotenza: lo stesso ordine inviato due volte produce un solo fill."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from backtest.costs import CostModel
from config.schema import CostsConfig
from core.clock import SimulatedClock
from core.types import Order, OrderStatus, OrderType, Side, SymbolFilters
from execution.broker import PaperBroker
from execution.idempotency import (
    MAX_CLIENT_ORDER_ID_LENGTH,
    DuplicateOrderError,
    OrderRegistry,
    client_order_id,
)

D = Decimal
NOW = 1_700_000_000_000


def make_order(
    *,
    quantity: str = "0.001",
    ts: int = NOW,
    side: Side = Side.BUY,
    symbol: str = "BTCUSDT",
    order_id: str | None = None,
) -> Order:
    return Order(
        client_order_id=order_id
        or client_order_id(
            symbol=symbol, side=side, quantity=D(quantity), decision_ts_ms=ts, intent="OPEN"
        ),
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=D(quantity),
        created_at_ms=ts,
    )


class TestClientOrderId:
    def test_deterministico(self) -> None:
        """La stessa decisione produce sempre lo stesso id."""
        first = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                decision_ts_ms=NOW, intent="OPEN")
        second = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                 decision_ts_ms=NOW, intent="OPEN")
        assert first == second

    def test_decisioni_diverse_id_diversi(self) -> None:
        base = dict(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"), decision_ts_ms=NOW)
        ids = {
            client_order_id(**base, intent="OPEN"),  # type: ignore[arg-type]
            client_order_id(**{**base, "quantity": D("0.002")}, intent="OPEN"),  # type: ignore[arg-type]
            client_order_id(**{**base, "side": Side.SELL}, intent="OPEN"),  # type: ignore[arg-type]
            client_order_id(**{**base, "symbol": "ETHUSDT"}, intent="OPEN"),  # type: ignore[arg-type]
            client_order_id(**base, intent="CLOSE"),  # type: ignore[arg-type]
        }
        assert len(ids) == 5

    def test_millisecondi_vicini_stesso_bucket(self) -> None:
        """Al riavvio la stessa decisione puo' ricalcolarsi con pochi ms di scarto."""
        first = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                decision_ts_ms=NOW, intent="OPEN")
        second = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                 decision_ts_ms=NOW + 300, intent="OPEN")
        assert first == second

    def test_secondi_diversi_id_diversi(self) -> None:
        first = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                decision_ts_ms=NOW, intent="OPEN")
        second = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                 decision_ts_ms=NOW + 5_000, intent="OPEN")
        assert first != second

    def test_rispetta_il_limite_di_binance(self) -> None:
        value = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.00012345"),
                                decision_ts_ms=NOW, intent="OPEN")
        assert len(value) <= MAX_CLIENT_ORDER_ID_LENGTH
        assert all(c.isalnum() or c in ".-_:/" for c in value)

    def test_quantita_equivalenti_stesso_id(self) -> None:
        """0.0010 e 0.001 sono la stessa quantita': devono dare lo stesso id."""
        first = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.0010"),
                                decision_ts_ms=NOW)
        second = client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("0.001"),
                                 decision_ts_ms=NOW)
        assert first == second

    def test_prefisso_troppo_lungo_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="massimo"):
            client_order_id(symbol="BTCUSDT", side=Side.BUY, quantity=D("1"),
                            decision_ts_ms=NOW, prefix="x" * 40)


class TestOrderRegistry:
    def test_registra_e_ritrova(self) -> None:
        registry = OrderRegistry()
        order = make_order()
        registry.reserve(order)
        assert order.client_order_id in registry
        assert registry.status_of(order.client_order_id) is OrderStatus.PENDING_NEW

    def test_doppia_registrazione_bloccata(self) -> None:
        """Prima linea di difesa: il duplicato non arriva nemmeno all'exchange."""
        registry = OrderRegistry()
        order = make_order()
        registry.reserve(order)
        with pytest.raises(DuplicateOrderError, match="gia' registrato"):
            registry.reserve(order)

    def test_ordini_in_volo_elencati(self) -> None:
        registry = OrderRegistry()
        first, second = make_order(), make_order(ts=NOW + 10_000)
        registry.reserve(first)
        registry.reserve(second)
        registry.update(first.client_order_id, status=OrderStatus.FILLED)
        assert registry.pending_ids() == [second.client_order_id]

    def test_sopravvive_al_riavvio(self, tmp_path: Path) -> None:
        """Il caso che conta: processo ucciso fra invio e conferma."""
        path = tmp_path / "orders.json"
        registry = OrderRegistry(path)
        order = make_order()
        registry.reserve(order)

        reloaded = OrderRegistry(path)
        assert order.client_order_id in reloaded
        assert reloaded.pending_ids() == [order.client_order_id]
        with pytest.raises(DuplicateOrderError):
            reloaded.reserve(order)

    def test_scrittura_atomica(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "orders.json"
        registry = OrderRegistry(path)
        registry.reserve(make_order())
        assert path.is_file()
        assert not path.with_suffix(".json.tmp").exists()
        assert json.loads(path.read_text())

    def test_registro_illeggibile_solleva(self, tmp_path: Path) -> None:
        """Senza registro ogni ordine in volo diventa invisibile: non si tace."""
        path = tmp_path / "orders.json"
        path.write_text("{ rotto", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            OrderRegistry(path)

    def test_pulizia_solo_dei_terminali(self) -> None:
        registry = OrderRegistry()
        done, flying = make_order(), make_order(ts=NOW + 10_000)
        registry.reserve(done)
        registry.reserve(flying)
        registry.update(done.client_order_id, status=OrderStatus.FILLED)
        removed = registry.forget([done.client_order_id, flying.client_order_id])
        assert removed == 1
        assert flying.client_order_id in registry

    def test_aggiornamento_di_un_id_ignoto(self) -> None:
        with pytest.raises(KeyError):
            OrderRegistry().update("mai-visto", status=OrderStatus.FILLED)


class TestPaperBrokerIdempotency:
    @pytest.fixture
    def broker(self) -> PaperBroker:
        filters = {
            "BTCUSDT": SymbolFilters(
                symbol="BTCUSDT", tick_size=D("0.01"), step_size=D("0.00001"),
                min_quantity=D("0.00001"), max_quantity=D("9000"), min_notional=D("5"),
                base_precision=5, quote_precision=2, fetched_at_ms=NOW,
            )
        }
        broker = PaperBroker(
            clock=SimulatedClock(NOW),
            costs=CostModel(CostsConfig()),
            filters=filters,
            balances={"USDT": D("200")},
        )
        broker.set_price("BTCUSDT", D("60000"))
        return broker

    @pytest.mark.asyncio
    async def test_stesso_ordine_due_volte_un_solo_fill(self, broker: PaperBroker) -> None:
        """Il test che da' il nome al file."""
        order = make_order()
        first = await broker.submit(order)
        second = await broker.submit(order)
        assert first.client_order_id == second.client_order_id
        assert len(broker.fills) == 1
        assert first.filled_quantity == second.filled_quantity

    @pytest.mark.asyncio
    async def test_il_saldo_si_muove_una_volta_sola(self, broker: PaperBroker) -> None:
        order = make_order()
        await broker.submit(order)
        after_first = broker.balances["USDT"]
        await broker.submit(order)
        assert broker.balances["USDT"] == after_first

    @pytest.mark.asyncio
    async def test_ordini_diversi_fill_diversi(self, broker: PaperBroker) -> None:
        await broker.submit(make_order(ts=NOW))
        await broker.submit(make_order(ts=NOW + 60_000))
        assert len(broker.fills) == 2

    @pytest.mark.asyncio
    async def test_ordine_sotto_il_minimo_rifiutato(self, broker: PaperBroker) -> None:
        tiny = make_order(quantity="0.00001")  # 0,6 di controvalore
        ack = await broker.submit(tiny)
        assert ack.status is OrderStatus.REJECTED
        assert not broker.fills
        assert "MIN_NOTIONAL" in str(ack.raw["motivo"])

    @pytest.mark.asyncio
    async def test_il_rifiuto_e_idempotente(self, broker: PaperBroker) -> None:
        tiny = make_order(quantity="0.00001")
        first = await broker.submit(tiny)
        second = await broker.submit(tiny)
        assert first.status is second.status
        assert len(broker.rejected) == 1

    @pytest.mark.asyncio
    async def test_le_fee_sono_addebitate(self, broker: PaperBroker) -> None:
        ack = await broker.submit(make_order())
        assert ack.fills[0].fee > 0

    @pytest.mark.asyncio
    async def test_senza_prezzo_non_esegue(self, broker: PaperBroker) -> None:
        from execution.broker import BrokerError

        with pytest.raises(BrokerError, match="nessun prezzo"):
            await broker.submit(make_order(symbol="ETHUSDT"))
