"""Integrity checks for the persisted order registry and delayed exchange replies."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.types import Order, OrderStatus, OrderType, Side
from execution.idempotency import OrderRegistry


def order() -> Order:
    return Order(
        client_order_id="order-1",
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        created_at_ms=1_700_000_000_000,
    )


def persisted_record() -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "1",
        "created_at_ms": 1_700_000_000_000,
        "status": "PARTIALLY_FILLED",
        "exchange_order_id": "exchange-1",
        "filled_quantity": "0.25",
    }


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("symbol", ""),
        ("side", "SIDEWAYS"),
        ("status", "UNKNOWN"),
        ("quantity", "0"),
        ("quantity", "NaN"),
        ("filled_quantity", "-0.1"),
        ("filled_quantity", "1.1"),
        ("filled_quantity", "Infinity"),
        ("created_at_ms", True),
        ("exchange_order_id", 42),
    ],
)
def test_load_rejects_malformed_order_record(
    tmp_path: Path, change: str, value: object
) -> None:
    record = persisted_record()
    record[change] = value
    path = tmp_path / "orders.json"
    path.write_text(json.dumps({"order-1": record}), encoding="utf-8")

    with pytest.raises(ValueError, match="registro ordini malformato"):
        OrderRegistry(path)


def test_load_rejects_missing_required_field(tmp_path: Path) -> None:
    record = persisted_record()
    del record["quantity"]
    path = tmp_path / "orders.json"
    path.write_text(json.dumps({"order-1": record}), encoding="utf-8")

    with pytest.raises(ValueError, match="registro ordini malformato"):
        OrderRegistry(path)


def test_failed_reload_preserves_valid_in_memory_records(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"
    registry = OrderRegistry(path)
    registry.reserve(order())
    invalid = persisted_record()
    invalid["filled_quantity"] = "2"
    path.write_text(json.dumps({"bad-order": invalid}), encoding="utf-8")

    with pytest.raises(ValueError, match="registro ordini malformato"):
        registry.load()

    assert registry.known_ids == {"order-1"}
    assert registry.status_of("order-1") is OrderStatus.PENDING_NEW


@pytest.mark.parametrize("quantity", [Decimal("-0.1"), Decimal("1.1"), Decimal("NaN")])
def test_invalid_fill_update_preserves_memory_and_disk(tmp_path: Path, quantity: Decimal) -> None:
    path = tmp_path / "orders.json"
    registry = OrderRegistry(path)
    registry.reserve(order())
    registry.update("order-1", status=OrderStatus.PARTIALLY_FILLED,
                    filled_quantity=Decimal("0.4"))
    before = path.read_bytes()

    with pytest.raises(ValueError):
        registry.update("order-1", status=OrderStatus.FILLED, filled_quantity=quantity)

    assert registry.status_of("order-1") is OrderStatus.PARTIALLY_FILLED
    assert registry.pending_quantities() == {"BTCUSDT": Decimal("0.6")}
    assert path.read_bytes() == before


def test_filled_quantity_cannot_go_backwards(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"
    registry = OrderRegistry(path)
    registry.reserve(order())
    registry.update("order-1", status=OrderStatus.PARTIALLY_FILLED,
                    filled_quantity=Decimal("0.4"))
    before = path.read_bytes()

    with pytest.raises(ValueError):
        registry.update("order-1", status=OrderStatus.NEW, filled_quantity=Decimal("0.2"))

    assert registry.status_of("order-1") is OrderStatus.PARTIALLY_FILLED
    assert registry.pending_quantities() == {"BTCUSDT": Decimal("0.6")}
    assert path.read_bytes() == before


def test_stale_open_status_cannot_resurrect_terminal_order(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"
    registry = OrderRegistry(path)
    registry.reserve(order())
    registry.update("order-1", status=OrderStatus.CANCELED,
                    filled_quantity=Decimal("0.4"))
    before = path.read_bytes()

    with pytest.raises(ValueError):
        registry.update("order-1", status=OrderStatus.PARTIALLY_FILLED,
                        filled_quantity=Decimal("0.5"))

    assert registry.status_of("order-1") is OrderStatus.CANCELED
    assert registry.pending_ids() == []
    assert path.read_bytes() == before


def test_later_fill_can_complete_canceled_partial_order(tmp_path: Path) -> None:
    path = tmp_path / "orders.json"
    registry = OrderRegistry(path)
    registry.reserve(order())
    registry.update("order-1", status=OrderStatus.CANCELED,
                    filled_quantity=Decimal("0.4"))

    registry.update("order-1", status=OrderStatus.FILLED,
                    filled_quantity=Decimal("1"))

    reloaded = OrderRegistry(path)
    assert reloaded.status_of("order-1") is OrderStatus.FILLED
    assert reloaded.pending_ids() == []
    assert json.loads(path.read_text(encoding="utf-8"))["order-1"]["filled_quantity"] == "1"
