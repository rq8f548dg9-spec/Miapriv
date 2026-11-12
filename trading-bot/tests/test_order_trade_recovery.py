"""Use actual order-query response shape: executedQty without a fills array."""
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from config.credentials import Credentials
from config.schema import Mode, load_config
from execution.binance import AmbiguousRequestError, HttpResponse
from tests.test_execution import FakeHttp, make_broker


@pytest.mark.asyncio
@pytest.mark.parametrize("trade_quantity", ["0.001", "0", "0.0005", "error"])
async def test_query_recovers_complete_trade_history_or_stays_unresolved(
    trade_quantity: str,
) -> None:
    order = {"symbol": "BTCUSDT", "clientOrderId": "recovery", "orderId": 123,
             "side": "BUY", "status": "FILLED", "executedQty": "0.001",
             "cummulativeQuoteQty": "60"}
    trades = [] if trade_quantity == "0" else [{
        "symbol": "BTCUSDT", "orderId": 123, "id": 456, "price": "60000",
        "qty": trade_quantity, "commission": "0.000001", "commissionAsset": "BTC",
        "time": 1700000000000, "isMaker": False, "isBuyer": True,
    }]
    history_response = (HttpResponse(400, {"code": -1, "msg": "history unavailable"})
                        if trade_quantity == "error" else HttpResponse(200, trades))
    http = FakeHttp([HttpResponse(200, order), history_response])
    broker = make_broker(load_config(Path("config/config.yaml")), Credentials(
        api_key=SecretStr("fixture-key"), api_secret=SecretStr("fixture-secret"),
        mode=Mode.TESTNET,
    ), http)
    if trade_quantity != "0.001":
        with pytest.raises(AmbiguousRequestError):
            await broker.order_status("BTCUSDT", "recovery")
    else:
        ack = await broker.order_status("BTCUSDT", "recovery")
        assert ack is not None
        assert len(ack.fills) == 1
        fill = ack.fills[0]
        assert fill.client_order_id == "recovery"
        assert fill.trade_id == "456"
        assert fill.quantity == Decimal("0.001")
        assert fill.fee == Decimal("0.000001")
        assert fill.fee_asset == "BTC"
        assert "/api/v3/myTrades?" in http.requests[1][1]
        assert "orderId=123" in http.requests[1][1]
