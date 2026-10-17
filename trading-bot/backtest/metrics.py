"""Metriche di performance.

Sono il metro con cui si decide se una strategia esiste. Due cautele messe
nel codice e non solo nei commenti:

* lo Sharpe annualizzato su poche osservazioni non significa niente: sotto
  un numero minimo di campioni viene restituito ``None``, non un numero;
* il profit factor di una strategia senza perdite e' infinito, e un infinito
  in un report e' sempre un errore di misura: viene restituito ``None``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from core.portfolio import TradeResult

__all__ = [
    "MIN_TRADES_FOR_VERDICT",
    "PerformanceMetrics",
    "Verdict",
    "assess",
    "compute_metrics",
    "risk_of_ruin",
]

#: Osservazioni minime sotto le quali Sharpe e Sortino non vengono calcolati.
MIN_SAMPLES_FOR_RATIO = 30

#: Operazioni minime sotto le quali non si esprime un giudizio. Con dieci
#: operazioni anche un +3% e' compatibile con la fortuna: dirlo "promosso"
#: o "bocciato" sarebbe inventare un'informazione che i dati non contengono.
MIN_TRADES_FOR_VERDICT = 20


def risk_of_ruin(win_rate: float, payoff_ratio: float, capital_units: float) -> float:
    """Probabilita' approssimata di azzerare il capitale.

    Approssimazione della rovina del giocatore con vincite e perdite di
    dimensione fissa: ``((1 - edge) / (1 + edge)) ** unita'``, dove l'edge e'
    il valore atteso per unita' rischiata. Vale come ordine di grandezza, non
    come previsione: assume operazioni indipendenti e size costante, due
    ipotesi che il mercato non rispetta mai del tutto.

    Args:
        win_rate: frazione di operazioni in profitto.
        payoff_ratio: vincita media / perdita media.
        capital_units: capitale diviso il rischio per operazione.
    """
    if not 0.0 <= win_rate <= 1.0:
        raise ValueError(f"win rate {win_rate} fuori da [0, 1]")
    if payoff_ratio <= 0 or capital_units <= 0:
        raise ValueError("payoff_ratio e capital_units devono essere positivi")

    edge = win_rate * payoff_ratio - (1.0 - win_rate)
    if edge <= 0:
        return 1.0  # senza edge la rovina e' certa, basta aspettare
    normalized = edge / payoff_ratio
    base = (1.0 - normalized) / (1.0 + normalized)
    return float(min(1.0, max(0.0, base**capital_units)))


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Risultato di una valutazione. I ``None`` sono dati mancanti, non zeri."""

    periods: int
    trades: int
    total_return: float
    sharpe: float | None
    sortino: float | None
    max_drawdown: float
    win_rate: float | None
    profit_factor: float | None
    expectancy: Decimal
    average_win: Decimal
    average_loss: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    total_fees: Decimal
    risk_of_ruin_estimate: float | None

    def summary(self) -> str:
        def fmt(value: float | None, digits: int = 2) -> str:
            return "n/d" if value is None else f"{value:.{digits}f}"

        return (
            f"periodi={self.periods} operazioni={self.trades} "
            f"rendimento={self.total_return:.2%} sharpe={fmt(self.sharpe)} "
            f"sortino={fmt(self.sortino)} maxDD={self.max_drawdown:.2%} "
            f"winrate={fmt(self.win_rate, 3)} PF={fmt(self.profit_factor)} "
            f"expectancy={self.expectancy:.4f} fee={self.total_fees:.4f}"
        )

    @property
    def is_viable(self) -> bool:
        """Soglia minima di decenza, non di bonta'.

        Delega ad ``assess`` per non avere due giudizi che possono
        contraddirsi: e' successo davvero, con un +2,6% su 14 operazioni
        annunciato come "non regge i costi".
        """
        return assess(self).outcome == "positivo"


@dataclass(frozen=True, slots=True)
class Verdict:
    """Giudizio su un backtest, con il motivo in chiaro.

    Tre esiti e non due: "insufficiente" non e' una bocciatura, e' l'assenza
    di dati per esprimersi. Confonderlo con "negativo" porta a scartare una
    strategia che non e' mai stata misurata.
    """

    outcome: str
    explanation: str

    @property
    def is_conclusive(self) -> bool:
        return self.outcome != "insufficiente"


def assess(metrics: PerformanceMetrics) -> Verdict:
    """Traduce le metriche in un giudizio motivato."""
    if metrics.trades < MIN_TRADES_FOR_VERDICT:
        return Verdict(
            "insufficiente",
            f"solo {metrics.trades} operazioni: troppo poche per concludere qualcosa, "
            f"ne servono almeno {MIN_TRADES_FOR_VERDICT}. Il rendimento di "
            f"{metrics.total_return:+.2%} su questo campione è compatibile con il caso.",
        )
    if metrics.total_return <= 0:
        return Verdict(
            "negativo",
            f"rendimento {metrics.total_return:+.2%} su {metrics.trades} operazioni: "
            "la strategia non copre i costi di transazione.",
        )
    if metrics.expectancy <= 0:
        return Verdict(
            "negativo",
            f"il guadagno medio per operazione e' {metrics.expectancy:+.4f}: "
            "negativo, quindi più opera e più perde.",
        )
    if metrics.profit_factor is not None and metrics.profit_factor <= 1.0:
        return Verdict(
            "negativo",
            f"profit factor {metrics.profit_factor:.2f}: le perdite pesano quanto "
            "o più dei guadagni.",
        )

    riserva = (
        " Il campione resta piccolo: serve una verifica walk-forward su più finestre."
        if metrics.trades < 100
        else ""
    )
    return Verdict(
        "positivo",
        f"rendimento {metrics.total_return:+.2%} su {metrics.trades} operazioni, "
        f"al netto di {metrics.total_fees} di commissioni.{riserva}",
    )


def compute_metrics(
    equity_curve: Sequence[tuple[int, Decimal]],
    trades: Sequence[TradeResult],
    *,
    total_fees: Decimal = Decimal(0),
    periods_per_year: float = 525_600.0,  # minuti in un anno: default su barre da 1m
    risk_per_trade_quote: Decimal | None = None,
) -> PerformanceMetrics:
    """Calcola le metriche da una curva di equity e dagli esiti delle operazioni."""
    if len(equity_curve) < 2:
        return _empty_metrics(trades, total_fees)

    values = [float(value) for _, value in equity_curve]
    returns = [
        values[i] / values[i - 1] - 1.0
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    total_return = values[-1] / values[0] - 1.0 if values[0] > 0 else 0.0

    sharpe = _annualized_ratio(returns, periods_per_year, downside_only=False)
    sortino = _annualized_ratio(returns, periods_per_year, downside_only=True)
    max_drawdown = _max_drawdown(values)

    wins = [t.realized_pnl for t in trades if t.realized_pnl > 0]
    losses = [t.realized_pnl for t in trades if t.realized_pnl < 0]
    gross_profit = sum(wins, Decimal(0))
    gross_loss = abs(sum(losses, Decimal(0)))

    win_rate = len(wins) / len(trades) if trades else None
    # Un profit factor infinito e' un errore di misura, non un risultato.
    profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else None
    expectancy = (
        sum((t.realized_pnl for t in trades), Decimal(0)) / len(trades) if trades else Decimal(0)
    )
    average_win = gross_profit / len(wins) if wins else Decimal(0)
    average_loss = gross_loss / len(losses) if losses else Decimal(0)

    ruin: float | None = None
    # Il rischio di rovina richiede sia vincite sia perdite: senza entrambe il
    # payoff ratio non e' definito e una stima sarebbe inventata.
    has_risk_unit = risk_per_trade_quote is not None and risk_per_trade_quote > 0
    if win_rate is not None and average_loss > 0 and average_win > 0 and has_risk_unit:
        assert risk_per_trade_quote is not None
        units = float(Decimal(str(values[0])) / risk_per_trade_quote)
        ruin = risk_of_ruin(win_rate, float(average_win / average_loss), units)

    return PerformanceMetrics(
        periods=len(returns),
        trades=len(trades),
        total_return=total_return,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        average_win=average_win,
        average_loss=average_loss,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        total_fees=total_fees,
        risk_of_ruin_estimate=ruin,
    )


def _annualized_ratio(
    returns: Sequence[float], periods_per_year: float, *, downside_only: bool
) -> float | None:
    """Sharpe o Sortino annualizzati, o ``None`` se i campioni non bastano."""
    if len(returns) < MIN_SAMPLES_FOR_RATIO:
        return None
    mean = sum(returns) / len(returns)
    if downside_only:
        downside = [r for r in returns if r < 0]
        if not downside:
            return None  # nessuna perdita: il rapporto non e' definito
        variance = sum(r**2 for r in downside) / len(downside)
    else:
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    deviation = math.sqrt(variance)
    if deviation <= 0:
        return None
    return mean / deviation * math.sqrt(periods_per_year)


def _max_drawdown(values: Sequence[float]) -> float:
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def _empty_metrics(trades: Sequence[TradeResult], total_fees: Decimal) -> PerformanceMetrics:
    return PerformanceMetrics(
        periods=0,
        trades=len(trades),
        total_return=0.0,
        sharpe=None,
        sortino=None,
        max_drawdown=0.0,
        win_rate=None,
        profit_factor=None,
        expectancy=Decimal(0),
        average_win=Decimal(0),
        average_loss=Decimal(0),
        gross_profit=Decimal(0),
        gross_loss=Decimal(0),
        total_fees=total_fees,
        risk_of_ruin_estimate=None,
    )
