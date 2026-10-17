"""Test del motore di backtest: costi, metriche e walk-forward."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from backtest.costs import CostModel
from backtest.engine import BacktestEngine
from backtest.metrics import compute_metrics, risk_of_ruin
from backtest.walkforward import run_walk_forward, split_windows
from config.schema import AppConfig, CostsConfig, WalkForwardConfig, load_config
from core.portfolio import TradeResult
from core.types import Candle, Side
from tests.test_no_lookahead import AlwaysTradingGenerator, synthetic_candles

D = Decimal
START = 1_700_000_000_000
DAY = 86_400_000


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


@pytest.fixture
def costs() -> CostModel:
    return CostModel(CostsConfig())


class TestCostModel:
    def test_lo_slippage_va_sempre_contro_chi_esegue(self, costs: CostModel) -> None:
        """Uno slippage simmetrico regalerebbe alla strategia meta' dei costi."""
        buy = costs.apply(side=Side.BUY, reference_price=D("100"), quantity=D("1"))
        sell = costs.apply(side=Side.SELL, reference_price=D("100"), quantity=D("1"))
        assert buy.fill_price > D("100")
        assert sell.fill_price < D("100")

    def test_lo_slippage_cresce_con_la_size(self, costs: CostModel) -> None:
        small = costs.slippage_bps(D("100"))
        large = costs.slippage_bps(D("100000"))
        assert large > small

    def test_impatto_concavo(self, costs: CostModel) -> None:
        """Raddoppiare la size non raddoppia l'impatto: la radice lo modella."""
        base = costs.slippage_bps(D("1000")) - costs.config.base_slippage_bps
        double = costs.slippage_bps(D("2000")) - costs.config.base_slippage_bps
        assert double < base * 2

    def test_il_maker_paga_meno_del_taker(self, costs: CostModel) -> None:
        maker = costs.apply(side=Side.BUY, reference_price=D("100"), quantity=D("1"), is_maker=True)
        taker = costs.apply(side=Side.BUY, reference_price=D("100"), quantity=D("1"))
        assert maker.fee < taker.fee

    def test_costo_del_giro_completo(self, costs: CostModel) -> None:
        """E' la soglia che un segnale deve battere per valere la pena."""
        round_trip = costs.round_trip_bps(D("5"))
        assert round_trip > 2 * costs.config.taker_fee_bps

    def test_la_fee_si_applica_al_prezzo_eseguito(self, costs: CostModel) -> None:
        cost = costs.apply(side=Side.BUY, reference_price=D("100"), quantity=D("2"))
        expected = cost.fill_price * D("2") * D(str(costs.config.taker_fee_bps)) / D("10000")
        assert cost.fee == expected

    def test_prezzo_non_positivo_rifiutato(self, costs: CostModel) -> None:
        with pytest.raises(ValueError):
            costs.apply(side=Side.BUY, reference_price=D("0"), quantity=D("1"))


class TestMetrics:
    def _curve(self, values: list[str]) -> list[tuple[int, Decimal]]:
        return [(START + i * 60_000, D(v)) for i, v in enumerate(values)]

    def test_curva_vuota_non_esplode(self) -> None:
        metrics = compute_metrics([], [])
        assert metrics.periods == 0
        assert metrics.sharpe is None

    def test_sharpe_non_calcolato_su_pochi_campioni(self) -> None:
        """Uno Sharpe su dieci osservazioni non significa niente."""
        curve = self._curve([str(100 + i) for i in range(10)])
        assert compute_metrics(curve, []).sharpe is None

    def test_sharpe_calcolato_con_abbastanza_campioni(self) -> None:
        curve = self._curve([str(100 + i * 0.1) for i in range(100)])
        metrics = compute_metrics(curve, [])
        assert metrics.sharpe is not None and metrics.sharpe > 0

    def test_max_drawdown(self) -> None:
        curve = self._curve(["100", "120", "90", "110"])
        assert compute_metrics(curve, []).max_drawdown == pytest.approx(0.25)

    def test_profit_factor_infinito_non_viene_riportato(self) -> None:
        """Un infinito in un report e' sempre un errore di misura."""
        trades = [TradeResult("BTCUSDT", START, D("10")) for _ in range(5)]
        assert compute_metrics(self._curve(["100", "150"]), trades).profit_factor is None

    def test_profit_factor_e_expectancy(self) -> None:
        trades = [
            TradeResult("BTCUSDT", START, D("10")),
            TradeResult("BTCUSDT", START, D("10")),
            TradeResult("BTCUSDT", START, D("-5")),
        ]
        metrics = compute_metrics(self._curve(["100", "115"]), trades)
        assert metrics.profit_factor == pytest.approx(4.0)
        assert metrics.expectancy == pytest.approx(D(5))
        assert metrics.win_rate == pytest.approx(2 / 3)

    def test_le_fee_entrano_nel_report(self) -> None:
        metrics = compute_metrics(self._curve(["100", "101"]), [], total_fees=D("0.37"))
        assert metrics.total_fees == D("0.37")

    def test_soglia_di_decenza(self) -> None:
        """is_viable risponde a una sola domanda: la baseline regge i costi?"""
        losing = compute_metrics(
            self._curve(["100", "90"]),
            [TradeResult("BTCUSDT", START, D("-1")) for _ in range(30)],
        )
        assert not losing.is_viable

    def test_rischio_di_rovina_senza_edge_e_certo(self) -> None:
        assert risk_of_ruin(win_rate=0.4, payoff_ratio=1.0, capital_units=40) == 1.0

    def test_rischio_di_rovina_scende_con_il_capitale(self) -> None:
        few = risk_of_ruin(win_rate=0.55, payoff_ratio=1.5, capital_units=5)
        many = risk_of_ruin(win_rate=0.55, payoff_ratio=1.5, capital_units=40)
        assert many < few

    def test_parametri_invalidi_rifiutati(self) -> None:
        with pytest.raises(ValueError):
            risk_of_ruin(win_rate=1.5, payoff_ratio=1.0, capital_units=10)


class TestBacktestEngine:
    def test_esegue_e_produce_metriche(self, config: AppConfig) -> None:
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(synthetic_candles(400))
        assert result.bars_processed == 400
        assert result.orders_submitted > 0
        assert result.metrics.periods > 0
        assert len(result.equity_curve) == 401

    def test_ogni_decisione_e_registrata(self, config: AppConfig) -> None:
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(synthetic_candles(400))
        assert len(result.decisions) == result.orders_submitted + result.orders_rejected
        for decision in result.decisions:
            assert decision.approved or decision.rejection_reasons

    def test_le_fee_erodono_lequity(self, config: AppConfig) -> None:
        """Principio 5: nessun risultato e' mai al lordo delle commissioni."""
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(synthetic_candles(400))
        assert result.metrics.total_fees > 0

    def test_dataset_vuoto_rifiutato(self, config: AppConfig) -> None:
        with pytest.raises(ValueError, match="nessuna candela"):
            BacktestEngine(config, AlwaysTradingGenerator()).run([])

    def test_senza_sezione_backtest_non_parte(self, config: AppConfig) -> None:
        """Senza il modello dei costi il backtest sarebbe una bugia."""
        without = config.model_copy(update={"backtest": None})
        with pytest.raises(ValueError, match="modello dei costi"):
            BacktestEngine(without, AlwaysTradingGenerator())

    def test_multi_simbolo(self, config: AppConfig) -> None:
        candles = synthetic_candles(300) + synthetic_candles(
            300, seed=42, symbol="ETHUSDT", start_price=D("3000")
        )
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        symbols = {fill.symbol for fill in result.fills}
        assert len(symbols) >= 1
        assert result.bars_processed == 600

    def test_i_limiti_di_rischio_valgono_nel_backtest(self, config: AppConfig) -> None:
        """Il backtest usa lo stesso risk manager del live, non una copia permissiva."""
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(synthetic_candles(600))
        assert result.orders_rejected > 0
        reasons = {r for d in result.decisions for r in d.rejection_reasons}
        assert reasons


class TestWalkForward:
    def test_finestre_scorrevoli(self) -> None:
        config = WalkForwardConfig(train_days=30, test_days=7, step_days=7,
                                   purge_bars=0, embargo_bars=0)
        windows = split_windows(START, START + 60 * DAY, config)
        assert len(windows) > 1
        assert windows[1].train_start_ms - windows[0].train_start_ms == 7 * DAY

    def test_purge_separa_train_e_test(self) -> None:
        """Le barre a cavallo vanno scartate: il loro esito cade dentro il test."""
        config = WalkForwardConfig(train_days=10, test_days=5, step_days=5,
                                   purge_bars=60, embargo_bars=0)
        window = split_windows(START, START + 40 * DAY, config)[0]
        assert window.test_start_ms - window.train_end_ms == 60 * 60_000

    def test_embargo_allontana_la_finestra_successiva(self) -> None:
        config = WalkForwardConfig(train_days=10, test_days=5, step_days=5,
                                   purge_bars=0, embargo_bars=120)
        windows = split_windows(START, START + 60 * DAY, config)
        gap = windows[1].train_start_ms - windows[0].train_start_ms
        assert gap == 5 * DAY + 120 * 60_000

    def test_le_finestre_non_escono_dal_periodo(self) -> None:
        config = WalkForwardConfig(train_days=30, test_days=7, step_days=7)
        windows = split_windows(START, START + 50 * DAY, config)
        assert all(w.test_end_ms <= START + 50 * DAY for w in windows)

    def test_periodo_vuoto_rifiutato(self) -> None:
        with pytest.raises(ValueError):
            split_windows(START, START, WalkForwardConfig())

    def test_esecuzione_completa(self, config: AppConfig) -> None:
        small = config.model_copy(
            update={
                "backtest": config.backtest.model_copy(  # type: ignore[union-attr]
                    update={
                        "walkforward": WalkForwardConfig(
                            train_days=1, test_days=1, step_days=1,
                            purge_bars=10, embargo_bars=10, min_trades_per_window=1,
                        )
                    }
                )
            }
        )
        candles = synthetic_candles(60 * 24 * 6)  # sei giorni di barre da un minuto
        report = run_walk_forward(
            small, candles, lambda cfg, train: BacktestEngine(cfg, AlwaysTradingGenerator())
        )
        assert report.windows
        assert report.consistency is not None
        assert "finestre valutate" in report.summary()

    def test_finestre_con_troppe_poche_operazioni_sono_segnalate(
        self, config: AppConfig
    ) -> None:
        """Un campione minuscolo non permette di concludere niente."""
        strict = config.model_copy(
            update={
                "backtest": config.backtest.model_copy(  # type: ignore[union-attr]
                    update={
                        "walkforward": WalkForwardConfig(
                            train_days=1, test_days=1, step_days=1,
                            purge_bars=0, embargo_bars=0, min_trades_per_window=10_000,
                        )
                    }
                )
            }
        )
        candles = synthetic_candles(60 * 24 * 4)
        report = run_walk_forward(
            strict, candles, lambda cfg, train: BacktestEngine(cfg, AlwaysTradingGenerator())
        )
        assert report.windows
        assert all(w.skipped_reason for w in report.windows)
        assert report.evaluated == []

    def test_il_train_arriva_alla_factory_senza_il_test(self, config: AppConfig) -> None:
        """Quando ci sara' un modello, non deve poter vedere i dati di test."""
        seen: list[tuple[int, int]] = []

        def factory(cfg: AppConfig, train: object) -> BacktestEngine:
            train_candles = list(train)  # type: ignore[call-overload]
            if train_candles:
                first: Candle = train_candles[0]
                last: Candle = train_candles[-1]
                seen.append((first.close_time_ms, last.close_time_ms))
            return BacktestEngine(cfg, AlwaysTradingGenerator())

        small = config.model_copy(
            update={
                "backtest": config.backtest.model_copy(  # type: ignore[union-attr]
                    update={
                        "walkforward": WalkForwardConfig(
                            train_days=1, test_days=1, step_days=1,
                            purge_bars=30, embargo_bars=0, min_trades_per_window=1,
                        )
                    }
                )
            }
        )
        candles = synthetic_candles(60 * 24 * 5)
        report = run_walk_forward(
            small, candles, factory
        )
        assert seen
        for (_, train_end), window_result in zip(seen, report.windows, strict=False):
            assert train_end < window_result.window.test_start_ms


class TestVerdetto:
    """Un rendimento positivo su pochissime operazioni non e' una bocciatura,
    ma non e' nemmeno una promozione: va detto quale dei due casi e'."""

    def _metrics(self, *, trades: int, ret: float, expectancy: str = "0.5"):  # type: ignore[no-untyped-def]
        from backtest.metrics import PerformanceMetrics

        return PerformanceMetrics(
            periods=14_000,
            trades=trades,
            total_return=ret,
            sharpe=None,
            sortino=None,
            max_drawdown=0.0008,
            win_rate=0.6,
            profit_factor=1.4,
            expectancy=D(expectancy),
            average_win=D("1"),
            average_loss=D("0.5"),
            gross_profit=D("10"),
            gross_loss=D("7"),
            total_fees=D("0.37"),
            risk_of_ruin_estimate=None,
        )

    def test_poche_operazioni_non_sono_una_bocciatura(self) -> None:
        """Il caso visto dal pannello: +2,60% su 14 operazioni."""
        from backtest.metrics import assess

        verdict = assess(self._metrics(trades=14, ret=0.026))
        assert verdict.outcome == "insufficiente"
        assert not verdict.is_conclusive
        assert "14" in verdict.explanation
        assert "non regge i costi" not in verdict.explanation.lower()

    def test_rendimento_negativo_e_una_bocciatura(self) -> None:
        from backtest.metrics import assess

        verdict = assess(self._metrics(trades=120, ret=-0.04))
        assert verdict.outcome == "negativo"
        assert verdict.is_conclusive

    def test_campione_ampio_e_positivo(self) -> None:
        from backtest.metrics import assess

        verdict = assess(self._metrics(trades=150, ret=0.03))
        assert verdict.outcome == "positivo"

    def test_guadagno_medio_negativo_bocciato(self) -> None:
        from backtest.metrics import assess

        verdict = assess(self._metrics(trades=150, ret=0.01, expectancy="-0.2"))
        assert verdict.outcome == "negativo"
        assert "operazione" in verdict.explanation

    def test_is_viable_resta_coerente_col_verdetto(self) -> None:
        """Una sola fonte di verita': niente due giudizi che si contraddicono."""
        from backtest.metrics import assess

        for trades, ret in ((14, 0.026), (150, 0.03), (150, -0.01)):
            metrics = self._metrics(trades=trades, ret=ret)
            assert metrics.is_viable == (assess(metrics).outcome == "positivo")
