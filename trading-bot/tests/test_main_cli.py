"""Il verdetto economico del backtest ha tre esiti, non due.

``--backtest`` li collassava in due: qualunque cosa non fosse "positivo"
usciva con codice 1 e con la frase "NON regge i costi di transazione".
Con poche operazioni quella frase e' falsa: il campione non basta per
dire nulla, ne' in bene ne' in male. Chi legge l'uscita (una persona o
uno script) non puo' distinguere una strategia bocciata da una mai
misurata, e scarta la seconda come fosse la prima.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import main as main_module
from backtest.metrics import PerformanceMetrics
from config.schema import AppConfig, load_config


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


def _metriche(*, trades: int, total_return: str, expectancy: str) -> PerformanceMetrics:
    return PerformanceMetrics(
        periods=1_000,
        trades=trades,
        total_return=float(total_return),
        sharpe=None,
        sortino=None,
        max_drawdown=0.01,
        win_rate=0.6,
        profit_factor=2.0,
        expectancy=Decimal(expectancy),
        average_win=Decimal("1"),
        average_loss=Decimal("-1"),
        gross_profit=Decimal("10"),
        gross_loss=Decimal("-5"),
        total_fees=Decimal("0.5"),
        risk_of_ruin_estimate=None,
    )


class _CandelaFinta:
    """Il motore non la guarda: serve solo a delimitare la finestra."""

    open_time_ms = 1_700_000_000_000
    close_time_ms = 1_700_000_059_999


class _RisultatoFinto:
    def __init__(self, metrics: PerformanceMetrics) -> None:
        self.metrics = metrics

    def summary(self) -> str:
        return "riepilogo finto"


def _prepara(
    monkeypatch: pytest.MonkeyPatch, metrics: PerformanceMetrics
) -> None:
    class StoreFinto:
        def __init__(self, *_: Any, **__: Any) -> None: ...

        def read_candles(self, **_: Any) -> list[Any]:
            return [_CandelaFinta()]

        def read_trades(self, **_: Any) -> list[Any]:
            return []

    class MotoreFinto:
        def __init__(self, *_: Any, **__: Any) -> None: ...

        def run(self, _candles: Any, trades: Any = ()) -> _RisultatoFinto:
            return _RisultatoFinto(metrics)

    monkeypatch.setattr(main_module, "ParquetStore", StoreFinto)
    monkeypatch.setattr(main_module, "BacktestEngine", MotoreFinto)
    monkeypatch.setattr(main_module, "build_generator", lambda _c: object())


def test_campione_insufficiente_non_e_una_bocciatura(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepara(monkeypatch, _metriche(trades=3, total_return="0.026", expectancy="0.01"))

    codice = main_module.run_backtest(config)
    uscita = capsys.readouterr().out

    assert codice == 3, "l'esito non conclusivo deve avere un codice tutto suo"
    assert "NON regge i costi" not in uscita
    assert "INSUFFICIENTE" in uscita
    assert "3 operazioni" in uscita


def test_esito_negativo_resta_una_bocciatura(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepara(monkeypatch, _metriche(trades=80, total_return="-0.04", expectancy="-0.01"))

    codice = main_module.run_backtest(config)
    uscita = capsys.readouterr().out

    assert codice == 1
    assert "NEGATIVA" in uscita


def test_esito_positivo_esce_pulito(
    config: AppConfig, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _prepara(monkeypatch, _metriche(trades=120, total_return="0.08", expectancy="0.02"))

    codice = main_module.run_backtest(config)
    uscita = capsys.readouterr().out

    assert codice == 0
    assert "POSITIVA" in uscita
