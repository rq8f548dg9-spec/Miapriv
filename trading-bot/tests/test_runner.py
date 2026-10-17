"""Test di integrazione del cablaggio: dal dato di mercato all'ordine eseguito."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backtest.costs import CostModel
from config.schema import AppConfig, CostsConfig, load_config
from core.clock import SimulatedClock
from core.portfolio import Portfolio
from core.types import Candle, SymbolFilters, Trade
from data.orderbook import DepthUpdate
from execution.broker import PaperBroker
from execution.idempotency import OrderRegistry
from monitoring.alerts import AlertManager
from monitoring.logger import DecisionLogger
from monitoring.state import StateStore
from runner import TradingBot, build_generator
from tests.test_no_lookahead import AlwaysTradingGenerator

D = Decimal
NOW = 1_700_000_000_000
MINUTE = 60_000


def filters_for(symbol: str) -> SymbolFilters:
    return SymbolFilters(
        symbol=symbol, tick_size=D("0.01"), step_size=D("0.00001"),
        min_quantity=D("0.00001"), max_quantity=D("9000"), min_notional=D("5"),
        base_precision=5, quote_precision=2, fetched_at_ms=NOW,
    )


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


@pytest.fixture
def bot(config: AppConfig, tmp_path: Path) -> TradingBot:
    clock = SimulatedClock(NOW)
    broker = PaperBroker(
        clock=clock,
        costs=CostModel(CostsConfig()),
        filters={"BTCUSDT": filters_for("BTCUSDT"), "ETHUSDT": filters_for("ETHUSDT")},
        balances={"USDT": D("200")},
    )
    broker.set_price("BTCUSDT", D("60000"))
    broker.set_price("ETHUSDT", D("3000"))
    instance = TradingBot(
        config=config,
        clock=clock,
        broker=broker,
        generator=AlwaysTradingGenerator(),
        portfolio=Portfolio(D("200")),
        registry=OrderRegistry(tmp_path / "orders.json"),
        alerts=AlertManager(clock),
        decision_log=DecisionLogger(mode="test"),
        state_store=StateStore(tmp_path / "state.json", clock, mode="test"),
        store=None,
    )
    instance.filters = {"BTCUSDT": filters_for("BTCUSDT"), "ETHUSDT": filters_for("ETHUSDT")}
    return instance


def feed_candles(bot: TradingBot, count: int, *, symbol: str = "BTCUSDT") -> None:
    """Riempie il warmup con candele crescenti."""
    price = D("60000")
    for i in range(count):
        price = (price * D("1.0005")).quantize(D("0.01"))
        bot.on_candle(
            Candle(
                symbol=symbol,
                open_time_ms=NOW + i * MINUTE,
                close_time_ms=NOW + (i + 1) * MINUTE - 1,
                open=price,
                high=price * D("1.001"),
                low=price * D("0.999"),
                close=price,
                volume=D("10"),
                quote_volume=price * D("10"),
                trades=25,
                is_closed=True,
            )
        )


class TestWiring:
    def test_il_generatore_viene_scelto_dalla_configurazione(self, config: AppConfig) -> None:
        assert build_generator(config).name == "rules"

    def test_i_book_sono_creati_per_i_simboli_abilitati(self, bot: TradingBot) -> None:
        assert set(bot.books) == {"BTCUSDT", "ETHUSDT"}

    def test_le_candele_aperte_sono_ignorate(self, bot: TradingBot) -> None:
        """Una candela in formazione non deve entrare nel feature engine."""
        bot.on_candle(
            Candle(
                symbol="BTCUSDT", open_time_ms=NOW, close_time_ms=NOW + MINUTE - 1,
                open=D("60000"), high=D("60100"), low=D("59900"), close=D("60050"),
                volume=D("1"), quote_volume=D("60000"), trades=1, is_closed=False,
            )
        )
        assert bot.features.state_for("BTCUSDT").bars_seen == 0

    def test_i_trade_alimentano_order_flow_e_prezzi(self, bot: TradingBot) -> None:
        bot.on_trade(Trade("BTCUSDT", 1, D("60500"), D("0.5"), NOW, False))
        assert bot.last_prices["BTCUSDT"] == D("60500")


class TestDecisionFlow:
    @pytest.mark.asyncio
    async def test_dalla_candela_allordine_eseguito(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        bot.clock.advance_by(MINUTE)  # type: ignore[attr-defined]
        order = await bot.evaluate_symbol("BTCUSDT")
        assert order is not None
        assert bot.portfolio.positions["BTCUSDT"].quantity > 0
        assert bot.decision_log.decisions_logged >= 1

    @pytest.mark.asyncio
    async def test_niente_ordini_prima_del_warmup(self, bot: TradingBot) -> None:
        feed_candles(bot, 10)
        assert await bot.evaluate_symbol("BTCUSDT") is None

    @pytest.mark.asyncio
    async def test_il_reinvio_e_bloccato_dal_registro(self, bot: TradingBot) -> None:
        """Due decisioni identiche nello stesso secondo: un solo ordine."""
        feed_candles(bot, 300)
        first = await bot.evaluate_symbol("BTCUSDT")
        assert first is not None
        fills_after_first = len(bot.broker.fills)  # type: ignore[attr-defined]
        await bot.submit(first)
        assert len(bot.broker.fills) == fills_after_first  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_senza_prezzo_non_si_decide(self, bot: TradingBot) -> None:
        feed_candles(bot, 300, symbol="ETHUSDT")
        bot.last_prices.pop("ETHUSDT", None)
        assert await bot.evaluate_symbol("ETHUSDT") is None


class TestResilienza:
    def test_la_disconnessione_invalida_i_book(self, bot: TradingBot) -> None:
        """Gli eventi persi durante la caduta non si recuperano."""
        book = bot.books["BTCUSDT"]
        book.apply_snapshot(
            last_update_id=10,
            bids=((D("60000"), D("1")),),
            asks=((D("60010"), D("1")),),
            ts_ms=NOW,
        )
        synced_before = book.is_synced
        bot.on_disconnect(NOW, "connessione persa")
        assert (synced_before, book.is_synced) == (True, False)
        assert "BTCUSDT" in bot.needs_resync
        assert bot.alerts.sent == 1

    def test_il_buco_di_sequenza_richiede_un_resync(self, bot: TradingBot) -> None:
        book = bot.books["BTCUSDT"]
        book.apply_snapshot(
            last_update_id=10, bids=((D("60000"), D("1")),), asks=((D("60010"), D("1")),),
            ts_ms=NOW,
        )
        bot.on_depth(DepthUpdate("BTCUSDT", 11, 20, (), (), NOW))
        assert "BTCUSDT" not in bot.needs_resync
        bot.on_depth(DepthUpdate("BTCUSDT", 50, 60, (), (), NOW))  # buco
        assert "BTCUSDT" in bot.needs_resync
        assert not book.is_synced

    def test_la_disconnessione_conta_per_il_kill_switch(self, bot: TradingBot) -> None:
        for i in range(bot.config.risk.killswitch.max_reconnects_per_hour):
            bot.on_disconnect(NOW + i, "caduta")
        events = bot.risk.killswitch.check(bot.portfolio.snapshot(NOW), now_ms=NOW)
        assert any(e.reason.value == "RECONNECT_STORM" for e in events)

    @pytest.mark.asyncio
    async def test_il_kill_switch_chiude_le_posizioni(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        await bot.evaluate_symbol("BTCUSDT")
        assert bot.portfolio.positions["BTCUSDT"].quantity > 0

        bot.risk.killswitch.trip_manually("stop di prova", bot.clock.now_ms())
        bot.clock.advance_by(60_000)  # type: ignore[attr-defined]
        await bot.check_killswitch()
        assert bot.portfolio.positions["BTCUSDT"].is_flat
        assert not bot.risk.killswitch.flatten_pending

    @pytest.mark.asyncio
    async def test_dopo_lo_stop_non_si_apre_piu(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        bot.risk.killswitch.trip_manually("stop", bot.clock.now_ms())
        bot.clock.advance_by(120_000)  # type: ignore[attr-defined]
        assert await bot.evaluate_symbol("BTCUSDT") is None


class TestStato:
    def test_lo_snapshot_viene_scritto(self, bot: TradingBot, tmp_path: Path) -> None:
        assert bot.snapshot_state(force=True)
        assert (tmp_path / "state.json").is_file()

    @pytest.mark.asyncio
    async def test_lo_stato_rispecchia_le_posizioni(self, bot: TradingBot) -> None:
        feed_candles(bot, 300)
        await bot.evaluate_symbol("BTCUSDT")
        bot.snapshot_state(force=True)
        state = bot.state_store.load()
        assert state is not None
        assert "BTCUSDT" in state.positions

    @pytest.mark.asyncio
    async def test_la_riconciliazione_periodica_gira(self, bot: TradingBot) -> None:
        await bot.periodic_reconcile()
        assert bot.reconciler.history
