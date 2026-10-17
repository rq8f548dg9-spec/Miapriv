"""Validazione walk-forward: train su N, test su N+1, scorrimento.

Ottimizzare i parametri sull'intero storico e poi misurarsi sullo stesso
storico non e' validazione, e' memoria. Il walk-forward misura cio' che
interessa davvero: come si comporta una configurazione scelta con i dati di
ieri sui dati di domani.

Due accorgimenti contro il leakage, entrambi in configurazione:

* **purge**: le barre a cavallo fra train e test vengono scartate, perche'
  l'esito di un'operazione aperta a fine train si realizza dentro il test;
* **embargo**: altre barre vengono scartate dopo il test, cosi' la finestra
  successiva non inizia dentro la coda di quella appena valutata.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from backtest.engine import BacktestEngine, BacktestResult
from config.schema import AppConfig, WalkForwardConfig
from core.types import Candle, TimestampMs

__all__ = ["WalkForwardReport", "Window", "WindowResult", "run_walk_forward", "split_windows"]

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class Window:
    """Una finestra di validazione, con i suoi confini temporali."""

    index: int
    train_start_ms: TimestampMs
    train_end_ms: TimestampMs
    test_start_ms: TimestampMs
    test_end_ms: TimestampMs

    @property
    def train_duration_ms(self) -> int:
        return self.train_end_ms - self.train_start_ms

    @property
    def test_duration_ms(self) -> int:
        return self.test_end_ms - self.test_start_ms


@dataclass(frozen=True, slots=True)
class WindowResult:
    """Esito di una finestra. ``skipped_reason`` spiega le finestre non valutate."""

    window: Window
    result: BacktestResult | None
    skipped_reason: str | None = None

    @property
    def evaluated(self) -> bool:
        """Una finestra scartata porta con se' il risultato per ispezione, ma non
        entra nelle statistiche: un campione sotto soglia non dimostra niente."""
        return self.result is not None and self.skipped_reason is None


@dataclass
class WalkForwardReport:
    """Aggregato delle finestre. Il numero che conta e' la coerenza, non la media."""

    windows: list[WindowResult]

    @property
    def evaluated(self) -> list[WindowResult]:
        return [w for w in self.windows if w.evaluated]

    @property
    def profitable_windows(self) -> int:
        return sum(
            1 for w in self.evaluated if w.result is not None and w.result.total_return > 0
        )

    @property
    def consistency(self) -> float | None:
        """Frazione di finestre in profitto.

        Una strategia che guadagna in una finestra su cinque e perde nelle
        altre quattro puo' avere un rendimento totale positivo e non essere
        affatto una strategia: e' una scommessa che e' andata bene una volta.
        """
        evaluated = self.evaluated
        if not evaluated:
            return None
        return self.profitable_windows / len(evaluated)

    @property
    def mean_return(self) -> float | None:
        evaluated = self.evaluated
        if not evaluated:
            return None
        return sum(w.result.total_return for w in evaluated if w.result) / len(evaluated)

    @property
    def worst_drawdown(self) -> float:
        return max(
            (w.result.metrics.max_drawdown for w in self.evaluated if w.result), default=0.0
        )

    @property
    def total_trades(self) -> int:
        return sum(w.result.metrics.trades for w in self.evaluated if w.result)

    def summary(self) -> str:
        consistency = self.consistency
        mean = self.mean_return
        return (
            f"finestre valutate={len(self.evaluated)}/{len(self.windows)} "
            f"in profitto={self.profitable_windows} "
            f"coerenza={'n/d' if consistency is None else f'{consistency:.0%}'} "
            f"rendimento medio={'n/d' if mean is None else f'{mean:.2%}'} "
            f"peggior DD={self.worst_drawdown:.2%} operazioni={self.total_trades}"
        )


def split_windows(
    start_ms: TimestampMs,
    end_ms: TimestampMs,
    config: WalkForwardConfig,
    *,
    bar_duration_ms: int = 60_000,
) -> list[Window]:
    """Genera le finestre scorrevoli con purge ed embargo applicati."""
    if end_ms <= start_ms:
        raise ValueError("finestra complessiva vuota")

    purge_ms = config.purge_bars * bar_duration_ms
    embargo_ms = config.embargo_bars * bar_duration_ms
    train_ms = config.train_days * DAY_MS
    test_ms = config.test_days * DAY_MS
    step_ms = config.step_days * DAY_MS

    windows: list[Window] = []
    train_start = start_ms
    index = 0
    while True:
        train_end = train_start + train_ms
        test_start = train_end + purge_ms
        test_end = test_start + test_ms
        if test_end > end_ms:
            break
        windows.append(
            Window(
                index=index,
                train_start_ms=train_start,
                train_end_ms=train_end,
                test_start_ms=test_start,
                test_end_ms=test_end,
            )
        )
        index += 1
        train_start += step_ms + embargo_ms
    return windows


def run_walk_forward(
    config: AppConfig,
    candles: Sequence[Candle],
    engine_factory: Callable[[AppConfig, Sequence[Candle]], BacktestEngine],
    *,
    bar_duration_ms: int = 60_000,
) -> WalkForwardReport:
    """Esegue il backtest su ogni finestra di test.

    ``engine_factory`` riceve le candele di TRAIN: e' li' dentro che un
    domani si addestrera' o si calibrera' il modello. Oggi la baseline a
    regole non si addestra, ma la firma esiste perche' quando ci sara' un
    modello non ci sia la tentazione di addestrarlo sui dati di test.
    """
    if config.backtest is None:
        raise ValueError("il walk-forward richiede la sezione 'backtest'")

    ordered = sorted(candles, key=lambda c: c.close_time_ms)
    if not ordered:
        raise ValueError("nessuna candela fornita")

    windows = split_windows(
        ordered[0].close_time_ms,
        ordered[-1].close_time_ms,
        config.backtest.walkforward,
        bar_duration_ms=bar_duration_ms,
    )

    results: list[WindowResult] = []
    for window in windows:
        train = [
            c for c in ordered if window.train_start_ms <= c.close_time_ms < window.train_end_ms
        ]
        test = [c for c in ordered if window.test_start_ms <= c.close_time_ms < window.test_end_ms]
        if not test:
            results.append(WindowResult(window, None, "nessuna candela nella finestra di test"))
            continue

        engine = engine_factory(config, train)
        result = engine.run(test)
        if result.metrics.trades < config.backtest.walkforward.min_trades_per_window:
            results.append(
                WindowResult(
                    window,
                    result,
                    f"solo {result.metrics.trades} operazioni, sotto il minimo "
                    f"{config.backtest.walkforward.min_trades_per_window}: "
                    "campione troppo piccolo per concludere qualcosa",
                )
            )
            continue
        results.append(WindowResult(window, result))

    return WalkForwardReport(results)


def total_equity_path(report: WalkForwardReport) -> list[tuple[TimestampMs, Decimal]]:
    """Concatena le curve di equity delle finestre valutate, per il grafico."""
    path: list[tuple[TimestampMs, Decimal]] = []
    for window_result in report.evaluated:
        if window_result.result is not None:
            path.extend(window_result.result.equity_curve)
    return sorted(path, key=lambda item: item[0])
