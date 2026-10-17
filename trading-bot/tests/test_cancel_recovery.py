"""Cancellation confirmation is not evidence that an order had no fills."""

from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import KillSwitchConfig, load_config
from core.types import Fill, Order, OrderStatus, OrderType, Side
from execution.broker import OrderAck
from risk.killswitch import KillSwitch
from tests.test_runner import NOW, make_bot


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [True, False])
async def test_cancel_recovers_racing_fills(tmp_path: Path, cancelled: bool) -> None:
    bot = make_bot(load_config(Path("config/config.yaml")), tmp_path)
    order = Order("racing", "BTCUSDT", Side.BUY, OrderType.LIMIT,
                  Decimal("0.002"), NOW, price=Decimal("60000"))
    bot.registry.reserve(order)
    fill = Fill("racing", "BTCUSDT", Side.BUY, Decimal("60000"), Decimal("0.001"),
                Decimal("0"), "USDT", NOW, False, "trade-racing")

    async def cancel(symbol: str, client_order_id: str) -> bool:
        return cancelled

    bot.broker.cancel = cancel  # type: ignore[method-assign]
    bot.broker.acks["racing"] = OrderAck(  # type: ignore[attr-defined]
        "racing", "exchange-racing", OrderStatus.CANCELED, Decimal("0.001"),
        Decimal("60000"), (fill,), 0,
    )
    await bot.cancel_pending_orders()
    assert bot.portfolio.positions["BTCUSDT"].quantity == Decimal("0.001")
    assert bot.portfolio.cash == Decimal("140")
    assert bot.registry.status_of("racing") is OrderStatus.CANCELED
    await bot.cancel_pending_orders()
    assert bot.portfolio.cash == Decimal("140")


@pytest.mark.asyncio
async def test_cancel_without_order_status_keeps_unknown_order_pending(tmp_path: Path) -> None:
    bot = make_bot(load_config(Path("config/config.yaml")), tmp_path)
    order = Order("unknown", "BTCUSDT", Side.BUY, OrderType.MARKET,
                  Decimal("0.001"), NOW)
    bot.registry.reserve(order)

    async def cancel(symbol: str, client_order_id: str) -> bool:
        return True

    bot.broker.cancel = cancel  # type: ignore[method-assign]
    bot.risk.killswitch.trip_manually("test", NOW)
    await bot.check_killswitch()
    assert bot.registry.pending_ids() == ["unknown"]
    assert bot.risk.killswitch.flatten_pending


def test_auto_resume_waits_for_confirmed_flatten() -> None:
    switch = KillSwitch(KillSwitchConfig(auto_resume=True, flatten_positions_on_trigger=True))
    switch.trip_manually("stop", NOW)
    after_cooldown = NOW + (switch.config.cooldown_minutes + 1) * 60_000
    assert not switch.allows_opening(after_cooldown)
    switch.mark_flattened()
    assert switch.allows_opening(after_cooldown)
