"""Test di logging strutturato, snapshot di stato e alert."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import load_config
from core.clock import SimulatedClock
from core.portfolio import Portfolio
from core.types import Decision, Fill, Order, OrderType, Side, Signal, SignalDirection
from monitoring.alerts import (
    AlertManager,
    CallbackChannel,
    FileChannel,
    LogChannel,
    Severity,
)
from monitoring.logger import DecisionLogger, JsonFormatter, setup_logging, to_jsonable
from monitoring.state import STATE_VERSION, StateStore
from risk.killswitch import KillSwitch

D = Decimal
NOW = 1_700_000_000_000


def sample_signal() -> Signal:
    return Signal(
        symbol="BTCUSDT",
        ts_ms=NOW,
        direction=SignalDirection.LONG,
        probability=0.68,
        features={"ofi_1s": 0.42, "vol_15": 0.0031, "atr_bps": 24.5},
        generator="rules",
        horizon_ms=300_000,
        metadata={"score": 0.77},
    )


def sample_order() -> Order:
    return Order(
        client_order_id="tb-abc",
        symbol="BTCUSDT",
        side=Side.BUY,
        order_type=OrderType.MARKET,
        quantity=D("0.001"),
        created_at_ms=NOW,
        metadata={"intent": "OPEN"},
    )


class TestSerialization:
    def test_i_decimal_diventano_stringhe(self) -> None:
        """Un prezzo che passa per float torna indietro diverso."""
        assert to_jsonable(D("60123.456789012345")) == "60123.456789012345"

    def test_strutture_annidate(self) -> None:
        payload = to_jsonable({"a": [D("1.5"), Side.BUY], "b": {"c": D("2")}})
        assert payload == {"a": ["1.5", "BUY"], "b": {"c": "2"}}

    def test_dataclass_serializzabile(self) -> None:
        payload = to_jsonable(sample_order())
        assert payload["client_order_id"] == "tb-abc"
        assert payload["quantity"] == "0.001"

    def test_il_risultato_e_json_valido(self) -> None:
        assert json.loads(json.dumps(to_jsonable(sample_signal())))


class TestDecisionLogger:
    def test_la_riga_contiene_tutte_le_feature(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Senza le feature che l'hanno generata, la decisione e' inspiegabile."""
        decision = Decision(
            ts_ms=NOW,
            symbol="BTCUSDT",
            signal=sample_signal(),
            approved=True,
            order=sample_order(),
            risk_snapshot={"equity": "200", "kelly_applied": 0.08},
            mode="testnet",
        )
        payload = DecisionLogger().log_decision(decision)
        assert payload["features"]["ofi_1s"] == 0.42
        assert payload["features"]["atr_bps"] == 24.5
        assert payload["signal"]["probability"] == 0.68
        assert payload["order"]["client_order_id"] == "tb-abc"
        assert payload["risk"]["equity"] == "200"

    def test_anche_i_rifiuti_vengono_scritti(self) -> None:
        """Un bot che non opera e di cui non si sa perche' non si puo' correggere."""
        decision = Decision(
            ts_ms=NOW,
            symbol="BTCUSDT",
            signal=sample_signal(),
            approved=False,
            rejection_reasons=("notional sotto il minimo", "correlazione troppo alta"),
            mode="testnet",
        )
        payload = DecisionLogger().log_decision(decision)
        assert payload["approved"] is False
        assert len(payload["rejection_reasons"]) == 2

    def test_conta_le_decisioni(self) -> None:
        decision_logger = DecisionLogger()
        for _ in range(3):
            decision_logger.log_decision(
                Decision(NOW, "BTCUSDT", None, False, rejection_reasons=("x",))
            )
        assert decision_logger.decisions_logged == 3

    def test_log_del_fill(self, caplog: pytest.LogCaptureFixture) -> None:
        fill = Fill("tb-abc", "BTCUSDT", Side.BUY, D("60000"), D("0.001"),
                    D("0.06"), "USDT", NOW, False, "t1")
        with caplog.at_level(logging.INFO):
            DecisionLogger().log_fill(fill, sample_order())
        record = caplog.records[-1]
        context = record.context  # type: ignore[attr-defined]
        assert context["event"] == "fill"
        assert context["price"] == "60000"
        assert context["intent"] == "OPEN"


class TestJsonFormatter:
    def test_una_riga_json_per_record(self) -> None:
        record = logging.LogRecord(
            "test", logging.INFO, "f.py", 1, "messaggio", None, None
        )
        record.context = {"event": "decision", "symbol": "BTCUSDT"}
        payload = json.loads(JsonFormatter().format(record))
        assert payload["message"] == "messaggio"
        assert payload["symbol"] == "BTCUSDT"
        assert payload["level"] == "INFO"

    def test_scrive_su_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "logs" / "bot.jsonl"
        setup_logging(level="INFO", json_logs=True, log_file=log_file)
        logging.getLogger("trading_bot.test").info("prova", extra={"context": {"a": 1}})
        logging.shutdown()
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert json.loads(lines[-1])["a"] == 1
        setup_logging(level="INFO", json_logs=False, log_file=None)  # ripristino


class TestStateStore:
    def _portfolio(self) -> Portfolio:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(
            Fill("c", "BTCUSDT", Side.BUY, D("60000"), D("0.001"), D("0.06"),
                 "USDT", NOW, False, "t")
        )
        return portfolio

    def test_cattura_e_salva(self, tmp_path: Path) -> None:
        config = load_config(Path("config/config.yaml"))
        store = StateStore(tmp_path / "state.json", SimulatedClock(NOW), mode="testnet")
        state = store.capture(self._portfolio(), KillSwitch(config.risk.killswitch))
        assert store.save(state, force=True)
        assert (tmp_path / "state.json").is_file()
        assert state.positions["BTCUSDT"] == "0.001"

    def test_roundtrip(self, tmp_path: Path) -> None:
        config = load_config(Path("config/config.yaml"))
        store = StateStore(tmp_path / "state.json", SimulatedClock(NOW), mode="testnet")
        original = store.capture(self._portfolio(), KillSwitch(config.risk.killswitch))
        store.save(original, force=True)
        reloaded = store.load()
        assert reloaded is not None
        assert reloaded.equity == original.equity
        assert reloaded.positions == original.positions

    def test_rispetta_la_cadenza(self, tmp_path: Path) -> None:
        config = load_config(Path("config/config.yaml"))
        clock = SimulatedClock(NOW)
        store = StateStore(tmp_path / "state.json", clock, interval_s=60)
        state = store.capture(self._portfolio(), KillSwitch(config.risk.killswitch))
        assert store.save(state, force=True)
        assert not store.save(state)
        clock.advance_by(61_000)
        assert store.save(store.capture(self._portfolio(), KillSwitch(config.risk.killswitch)))

    def test_file_corrotto_non_blocca_lavvio(self, tmp_path: Path) -> None:
        """Lo stato vero si ricostruisce dall'exchange: un file rotto non ferma nulla."""
        path = tmp_path / "state.json"
        path.write_text("{ rotto", encoding="utf-8")
        assert StateStore(path, SimulatedClock(NOW)).load() is None

    def test_versione_diversa_rifiutata(self, tmp_path: Path) -> None:
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"version": STATE_VERSION + 1}), encoding="utf-8")
        assert StateStore(path, SimulatedClock(NOW)).load() is None

    def test_stato_assente(self, tmp_path: Path) -> None:
        assert StateStore(tmp_path / "manca.json", SimulatedClock(NOW)).load() is None

    def test_scrittura_atomica(self, tmp_path: Path) -> None:
        config = load_config(Path("config/config.yaml"))
        path = tmp_path / "nested" / "state.json"
        store = StateStore(path, SimulatedClock(NOW))
        store.save(store.capture(self._portfolio(), KillSwitch(config.risk.killswitch)), force=True)
        assert path.is_file()
        assert not path.with_suffix(".json.tmp").exists()

    def test_lo_stato_registra_il_kill_switch(self, tmp_path: Path) -> None:
        config = load_config(Path("config/config.yaml"))
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_manually("stop di prova", NOW)
        store = StateStore(tmp_path / "state.json", SimulatedClock(NOW))
        state = store.capture(self._portfolio(), switch)
        assert state.killswitch_tripped
        assert state.killswitch_reason is not None
        assert "stop di prova" in state.killswitch_reason


class TestAlerts:
    def test_invia_sul_canale_di_default(self, caplog: pytest.LogCaptureFixture) -> None:
        manager = AlertManager(SimulatedClock(NOW))
        with caplog.at_level(logging.WARNING):
            assert manager.notify("k", "titolo", "dettaglio")
        assert "titolo" in caplog.text

    def test_deduplica_entro_la_finestra(self) -> None:
        """Lo stesso allarme ogni secondo smette di essere letto."""
        clock = SimulatedClock(NOW)
        manager = AlertManager(clock, dedup_window_s=300)
        assert manager.notify("k", "t", "d")
        assert not manager.notify("k", "t", "d")
        assert manager.suppressed == 1

    def test_dopo_la_finestra_rinvia(self) -> None:
        clock = SimulatedClock(NOW)
        manager = AlertManager(clock, dedup_window_s=60)
        manager.notify("k", "t", "d")
        clock.advance_by(61_000)
        assert manager.notify("k", "t", "d")
        assert manager.sent == 2

    def test_chiavi_diverse_non_si_deduplicano(self) -> None:
        manager = AlertManager(SimulatedClock(NOW))
        assert manager.notify("a", "t", "d")
        assert manager.notify("b", "t", "d")

    def test_disattivato_non_invia(self) -> None:
        manager = AlertManager(SimulatedClock(NOW), enabled=False)
        assert not manager.notify("k", "t", "d")

    def test_kill_switch_e_sempre_critico(self) -> None:
        config = load_config(Path("config/config.yaml"))
        switch = KillSwitch(config.risk.killswitch)
        event = switch.trip_manually("drawdown", NOW)
        manager = AlertManager(SimulatedClock(NOW))
        assert manager.killswitch_triggered(event)
        assert manager.history[-1].severity is Severity.CRITICAL

    def test_canale_su_file(self, tmp_path: Path) -> None:
        path = tmp_path / "alerts.log"
        manager = AlertManager(SimulatedClock(NOW), channels=[FileChannel(path)])
        manager.notify("k", "titolo", "dettaglio", Severity.CRITICAL)
        assert "titolo" in path.read_text(encoding="utf-8")

    def test_canale_rotto_non_ferma_il_bot(self) -> None:
        """Un'integrazione che fallisce non deve far cadere il trading."""

        def broken(_: str) -> None:
            raise ConnectionError("telegram giu'")

        channel = CallbackChannel(sender=broken, name="telegram")
        manager = AlertManager(SimulatedClock(NOW), channels=[channel, LogChannel()])
        assert manager.notify("k", "t", "d", Severity.CRITICAL)
        assert channel.failures == 1

    def test_canale_filtra_per_gravita(self) -> None:
        received: list[str] = []
        channel = CallbackChannel(sender=received.append, min_severity=Severity.CRITICAL)
        manager = AlertManager(SimulatedClock(NOW), channels=[channel])
        manager.notify("a", "info", "d", Severity.INFO)
        manager.notify("b", "critico", "d", Severity.CRITICAL)
        assert len(received) == 1
        assert "critico" in received[0]

    def test_divergenza_di_stato_notificata(self) -> None:
        manager = AlertManager(SimulatedClock(NOW))
        assert manager.reconciliation_failed("BTCUSDT: locale 0, exchange 0.5")
        assert manager.history[-1].severity is Severity.CRITICAL
