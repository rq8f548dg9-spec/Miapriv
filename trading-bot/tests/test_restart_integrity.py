"""Restart must preserve daily risk references and reject unreadable protection state."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import load_config
from core.clock import SimulatedClock
from tests.test_runner import NOW, make_bot


@pytest.mark.asyncio
async def test_same_day_restart_does_not_erase_daily_loss(tmp_path: Path) -> None:
    config = load_config(Path("config/config.yaml"))
    first = make_bot(config, tmp_path)
    first.portfolio.day_start_equity = Decimal("201")
    first.snapshot_state(force=True)
    restarted = make_bot(config, tmp_path)
    restarted.running = True
    await restarted._supervise(max_iterations=1)
    assert restarted.portfolio.day_start_equity == Decimal("201")
    assert restarted.portfolio.snapshot(NOW).daily_drawdown_pct == pytest.approx(1 / 201)


@pytest.mark.asyncio
async def test_next_day_rolls_reference_before_daily_kill_check(tmp_path: Path) -> None:
    config = load_config(Path("config/config.yaml"))
    first = make_bot(config, tmp_path)
    first.portfolio.day_start_equity = Decimal("250")
    first.snapshot_state(force=True)
    restarted = make_bot(config, tmp_path)
    assert isinstance(restarted.clock, SimulatedClock)
    restarted.clock.advance_to(NOW + 86_400_000)
    restarted.running = True
    await restarted._supervise(max_iterations=1)
    assert restarted.portfolio.day_start_equity == Decimal("200")
    assert not restarted.risk.killswitch.is_tripped


@pytest.mark.parametrize("contents", ["{ broken", '{"version": 999}', "[]"])
def test_existing_unreadable_state_blocks_bot_construction(
    tmp_path: Path, contents: str
) -> None:
    (tmp_path / "state.json").write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="stato"):
        make_bot(load_config(Path("config/config.yaml")), tmp_path)


def test_unreadable_exit_plan_does_not_silently_disappear(tmp_path: Path) -> None:
    config = load_config(Path("config/config.yaml"))
    first = make_bot(config, tmp_path)
    first.snapshot_state(force=True)
    path = tmp_path / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["exit_plans"] = {"BTCUSDT": {"entry_price": "60000"}}
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="piano"):
        make_bot(config, tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("day_start_equity", "NaN"), ("peak_equity", "-1"),
     ("killswitch_tripped", "false"), ("saved_at_ms", -1)],
)
def test_semantically_invalid_protection_state_blocks_restart(
    tmp_path: Path, field: str, value: object
) -> None:
    config = load_config(Path("config/config.yaml"))
    make_bot(config, tmp_path).snapshot_state(force=True)
    path = tmp_path / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state[field] = value
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="stato"):
        make_bot(config, tmp_path)


@pytest.mark.parametrize("timestamp", [None, -1, True, "1700000000000"])
def test_tripped_snapshot_requires_valid_trip_timestamp(
    tmp_path: Path, timestamp: object
) -> None:
    config = load_config(Path("config/config.yaml"))
    make_bot(config, tmp_path).snapshot_state(force=True)
    path = tmp_path / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["killswitch_tripped"] = True
    state["killswitch_tripped_at_ms"] = timestamp
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="stato"):
        make_bot(config, tmp_path)


@pytest.mark.parametrize("day", ["invalid", "", "2099-01-01", 42])
def test_invalid_risk_day_cannot_reset_daily_loss(tmp_path: Path, day: object) -> None:
    config = load_config(Path("config/config.yaml"))
    make_bot(config, tmp_path).snapshot_state(force=True)
    path = tmp_path / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["extra"]["risk_day_key"] = day
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="stato"):
        make_bot(config, tmp_path)
