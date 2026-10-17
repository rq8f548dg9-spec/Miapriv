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
from core.portfolio import Portfolio, TradeResult
from core.types import Candle, Order, OrderType, Side, SignalDirection, TimestampMs
from features.engine import FeatureVector
from signals.base import GeneratedSignal, SignalGenerator, TradePlan
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


class SoloAcquisti(SignalGenerator):
    """Compra e non vende mai. Cosi' qualunque vendita nel risultato puo'
    venire solo dalla chiusura d'emergenza, e il test non puo' passare per
    il motivo sbagliato."""

    name = "solo-acquisti"

    #: Se falso compra a ogni barra, anche con una posizione gia' aperta.
    solo_se_piatto = True

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        if not self.warmup_satisfied(vector):
            return None
        if has_position and self.solo_se_piatto:
            return None
        return GeneratedSignal(
            signal=self._build_signal(
                symbol=vector.symbol,
                now_ms=now_ms,
                direction=SignalDirection.LONG,
                probability=0.72,
                features=vector.values,
                generator=self.name,
                horizon_ms=300_000,
            ),
            plan=TradePlan(stop_bps=40.0, take_profit_bps=80.0),
        )


def crollo(count: int, *, start_price: Decimal, start_ms: int, passo: str) -> list[Candle]:
    """Serie che scende di una percentuale fissa a ogni barra."""
    price = start_price
    candles: list[Candle] = []
    for i in range(count):
        open_price = price
        price = (price * (D(1) - D(passo))).quantize(D("0.01"))
        candles.append(
            Candle(
                symbol="BTCUSDT",
                open_time_ms=start_ms + i * 60_000,
                close_time_ms=start_ms + (i + 1) * 60_000 - 1,
                open=open_price,
                high=open_price,
                low=price,
                close=price,
                volume=D("12"),
                quote_volume=price * D("12"),
                trades=40,
                is_closed=True,
            )
        )
    return candles


class TestChiusuraDEmergenzaNelBacktest:
    def test_lo_stop_chiude_le_posizioni_anche_nel_backtest(
        self, config: AppConfig
    ) -> None:
        """A24: il kill switch scattava e il backtest restava investito. La
        protezione simulata non somigliava a quella vera, e il risultato
        storico conteneva esposizione che dal vivo non sarebbe esistita."""
        switch = config.risk.killswitch.model_copy(
            update={"max_daily_drawdown_pct": 0.001, "flatten_positions_on_trigger": True}
        )
        stretto = config.model_copy(
            update={"risk": config.risk.model_copy(update={"killswitch": switch})}
        )
        candles = synthetic_candles(300) + crollo(
            40, start_price=D("60000"), start_ms=START + 300 * 60_000, passo="0.02"
        )
        result = BacktestEngine(stretto, SoloAcquisti()).run(candles)

        acquisti = [f for f in result.fills if f.side is Side.BUY]
        vendite = [f for f in result.fills if f.side is Side.SELL]
        assert acquisti, "il generatore non ha comprato: il test non prova niente"
        assert vendite, "posizione ancora aperta dopo lo stop"
        residuo = sum(f.quantity for f in acquisti) - sum(f.quantity for f in vendite)
        assert residuo == 0, f"restano {residuo} in portafoglio dopo lo stop"


class TestPianoDiUscitaNelBacktest:
    def test_lo_stop_chiude_anche_nella_simulazione(self, config: AppConfig) -> None:
        """Se dal vivo la posizione esce allo stop e nel backtest no, i due
        sistemi non sono lo stesso sistema e il risultato non vale."""
        discesa = [
            *synthetic_candles(300),
            *crollo(
                20, start_price=D("60000"), start_ms=START + 300 * 60_000, passo="0.01"
            ),
        ]
        compratore = SoloAcquisti()  # compra una volta e non vende mai
        risultato = BacktestEngine(config, compratore).run(discesa)

        acquisti = [f for f in risultato.fills if f.side is Side.BUY]
        vendite = [f for f in risultato.fills if f.side is Side.SELL]
        assert acquisti, "senza acquisti il test non prova niente"
        assert vendite, "la posizione non e' uscita allo stop"


class TestEsecuzionePossibile:
    def _ordine(self, quantita: str) -> Order:
        return Order(
            client_order_id="bt-prova", symbol="BTCUSDT", side=Side.BUY,
            order_type=OrderType.MARKET, quantity=D(quantita), created_at_ms=START,
            metadata={"reference_price": "60000"},
        )

    def _barra(self, apertura: str) -> Candle:
        return Candle(
            symbol="BTCUSDT", open_time_ms=START, close_time_ms=START + 59_999,
            open=D(apertura), high=D(apertura), low=D(apertura), close=D(apertura),
            volume=D("12"), quote_volume=D("1"), trades=40, is_closed=True,
        )

    def test_un_salto_di_prezzo_non_fa_spendere_piu_del_conto(
        self, config: AppConfig
    ) -> None:
        """A34: l'ordine deciso a 60.000 veniva eseguito a 6.000.000 senza
        ricontrollare niente, lasciando il conto simulato sotto zero. Un
        conto negativo non esiste su un exchange a pronti, e un backtest che
        lo ammette misura guadagni che nessuno avrebbe potuto fare."""
        engine = BacktestEngine(config, SoloAcquisti())
        portfolio = Portfolio(D("200"))
        fill, motivo = engine.try_execute(
            self._ordine("0.0001"), self._barra("6000000"), portfolio, START
        )
        assert fill is None
        assert "liquidit" in motivo.lower(), motivo

    def test_un_ordine_sostenibile_viene_eseguito(self, config: AppConfig) -> None:
        engine = BacktestEngine(config, SoloAcquisti())
        portfolio = Portfolio(D("200"))
        fill, motivo = engine.try_execute(
            self._ordine("0.0001"), self._barra("60000"), portfolio, START
        )
        assert fill is not None, motivo
        assert fill.quantity == D("0.0001")

    def test_non_si_vende_piu_di_quanto_si_possiede(self, config: AppConfig) -> None:
        engine = BacktestEngine(config, SoloAcquisti())
        portfolio = Portfolio(D("200"))
        vendita = Order(
            client_order_id="bt-vendita", symbol="BTCUSDT", side=Side.SELL,
            order_type=OrderType.MARKET, quantity=D("0.001"), created_at_ms=START,
        )
        fill, motivo = engine.try_execute(vendita, self._barra("60000"), portfolio, START)
        assert fill is None
        assert "posseduto" in motivo, motivo

    def test_la_simulazione_non_va_mai_sotto_zero(self, config: AppConfig) -> None:
        salto = [
            *synthetic_candles(300),
            Candle(
                symbol="BTCUSDT",
                open_time_ms=START + 300 * 60_000,
                close_time_ms=START + 301 * 60_000 - 1,
                open=D("6000000"),  # apertura cento volte sopra la chiusura precedente
                high=D("6000000"), low=D("6000000"), close=D("6000000"),
                volume=D("12"), quote_volume=D("72000000"), trades=40, is_closed=True,
            )
        ]
        compratore = SoloAcquisti()
        compratore.solo_se_piatto = False  # decide a ogni barra, gap compreso
        # Senza freno di frequenza: serve un ordine deciso sulla barra prima
        # del salto, altrimenti non c'e' niente da eseguire al prezzo nuovo.
        limiti = config.risk.limits.model_copy(
            update={"max_orders_per_symbol_per_hour": 10_000, "min_seconds_between_orders": 0}
        )
        libero = config.model_copy(
            update={"risk": config.risk.model_copy(update={"limits": limiti})}
        )
        result = BacktestEngine(libero, compratore).run(salto)
        assert result.final_equity > 0
        for _, equity in result.equity_curve:
            assert equity >= 0, "il conto simulato e' andato sotto zero"


def _con_costi(config: AppConfig, **modifiche: object) -> AppConfig:
    """Copia della configurazione con il modello dei costi modificato."""
    backtest = config.backtest
    assert backtest is not None, "config.yaml ha sempre la sezione backtest"
    costi = backtest.costs.model_copy(update=modifiche)
    return config.model_copy(
        update={"backtest": backtest.model_copy(update={"costs": costi})}
    )


class TestLatenzaERifiuti:
    """A25: cambiare la latenza da 150 ms a sedici minuti non cambiava
    niente, e la quota di rifiuto degli ordini limite non esisteva proprio.
    Due parametri che promettevano un modello che non c'era."""

    def test_una_latenza_oltre_la_barra_ritarda_lesecuzione(
        self, config: AppConfig
    ) -> None:
        candele = synthetic_candles(320)
        veloce = BacktestEngine(config, SoloAcquisti()).run(candele)
        lenta = _con_costi(config, latency_ms=600_000)
        risultato = BacktestEngine(lenta, SoloAcquisti()).run(candele)

        assert veloce.fills, "senza esecuzioni il confronto non dice niente"
        assert risultato.fills, "con la latenza lunga non si esegue piu' niente"

        def ritardo(risultato_backtest: object, indice: int = 0) -> int:
            esecuzione = risultato_backtest.fills[indice]  # type: ignore[attr-defined]
            decisione = next(
                d
                for d in risultato_backtest.decisions  # type: ignore[attr-defined]
                if d.order is not None
                and d.order.client_order_id == esecuzione.client_order_id
            )
            return int(esecuzione.ts_ms - decisione.ts_ms)

        # candele da un minuto: senza latenza si esegue all'apertura dopo
        assert ritardo(veloce) <= 60_001
        # dieci minuti di latenza: dieci minuti dopo la decisione
        assert ritardo(risultato) >= 600_000

    def test_la_quota_di_rifiuto_si_applica_agli_ordini_limite(
        self, config: AppConfig
    ) -> None:
        """Con l'ipotesi maker, una parte degli ordini non viene eseguita:
        un ordine limite al prezzo migliore non e' garantito."""
        candele = synthetic_candles(400)
        engine = BacktestEngine(
            _con_costi(config, limit_order_reject_rate=0.99),
            SoloAcquisti(),
            assume_maker=True,
        )
        risultato = engine.run(candele)
        assert risultato.orders_submitted > 0
        assert risultato.orders_unfilled > 0

    def test_senza_ipotesi_maker_nessun_rifiuto_simulato(
        self, config: AppConfig
    ) -> None:
        """Il motore invia ordini a mercato: la quota di rifiuto degli
        ordini limite non li riguarda."""
        a_mercato = _con_costi(config, limit_order_reject_rate=0.99)
        risultato = BacktestEngine(a_mercato, SoloAcquisti()).run(
            synthetic_candles(400)
        )
        assert risultato.orders_unfilled == 0


class TestOrdiniInVoloNelBacktest:
    def test_non_apre_piu_posizioni_di_quante_ne_permette(
        self, config: AppConfig
    ) -> None:
        """A33: con "una posizione al massimo" il backtest ne apriva due,
        BTC ed ETH, perche' l'ordine gia' deciso non occupava nessuno slot
        finche' non veniva eseguito alla barra dopo."""
        limiti = config.risk.limits.model_copy(update={"max_open_positions": 1})
        stretto = config.model_copy(
            update={"risk": config.risk.model_copy(update={"limits": limiti})}
        )
        candles = synthetic_candles(300) + synthetic_candles(
            300, seed=42, symbol="ETHUSDT", start_price=D("3000")
        )
        result = BacktestEngine(stretto, AlwaysTradingGenerator()).run(candles)
        quantita: dict[str, Decimal] = {}
        massimo = 0
        for fill in sorted(result.fills, key=lambda f: f.ts_ms):
            segno = D(1) if fill.side is Side.BUY else D(-1)
            quantita[fill.symbol] = quantita.get(fill.symbol, D(0)) + segno * fill.quantity
            massimo = max(massimo, sum(1 for q in quantita.values() if q > 0))
        assert massimo <= 1, f"fino a {massimo} simboli aperti insieme"


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
