"""Test del risk manager.

E' il layer di sicurezza: viene testato per primo e piu' a fondo di tutto il
resto. Ogni test descrive un modo concreto in cui il bot potrebbe perdere
denaro se il controllo non ci fosse.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import AppConfig, load_config
from core.clock import SimulatedClock
from core.portfolio import Portfolio, PortfolioSnapshot
from core.types import (
    Fill,
    Order,
    Side,
    Signal,
    SignalDirection,
    SymbolFilters,
)
from risk.killswitch import KillSwitch, TriggerReason
from risk.limits import (
    CorrelationTracker,
    LimitChecker,
    OrderIntent,
    OrderProposal,
    OrderRateTracker,
)
from risk.manager import MarketContext, RiskManager
from risk.sizing import PositionSizer, kelly_fraction

D = Decimal
NOW = 1_700_000_000_000
BTC_PRICE = D("60000")


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


@pytest.fixture
def filters() -> SymbolFilters:
    """Filtri realistici di BTCUSDT, come li restituisce exchangeInfo."""
    return SymbolFilters(
        symbol="BTCUSDT",
        tick_size=D("0.01"),
        step_size=D("0.00001"),
        min_quantity=D("0.00001"),
        max_quantity=D("9000"),
        min_notional=D("5"),
        base_precision=5,
        quote_precision=2,
        fetched_at_ms=NOW,
    )


@pytest.fixture
def clock() -> SimulatedClock:
    return SimulatedClock(NOW)


@pytest.fixture
def manager(config: AppConfig, clock: SimulatedClock) -> RiskManager:
    counter = {"n": 0}

    def make_id(symbol: str, side: Side, ts_ms: int) -> str:
        counter["n"] += 1
        return f"{symbol}-{side.value}-{ts_ms}-{counter['n']}"

    return RiskManager.from_config(config, clock, make_id)


def snapshot_of(portfolio: Portfolio, ts_ms: int = NOW) -> PortfolioSnapshot:
    return portfolio.snapshot(ts_ms)


def long_signal(probability: float = 0.70, symbol: str = "BTCUSDT") -> Signal:
    return Signal(
        symbol=symbol,
        ts_ms=NOW,
        direction=SignalDirection.LONG,
        probability=probability,
        features={"ofi_1s": 0.42, "vol_15": 0.0031},
        generator="rules",
        horizon_ms=300_000,
    )


def context(filters: SymbolFilters, **overrides: object) -> MarketContext:
    kwargs: dict[str, object] = dict(
        price=BTC_PRICE,
        filters=filters,
        payoff_ratio=1.5,
        expected_move_bps=50.0,
        estimated_cost_bps=11.0,
    )
    kwargs.update(overrides)
    return MarketContext(**kwargs)  # type: ignore[arg-type]


def buy_fill(
    quantity: str, price: Decimal = BTC_PRICE, ts: int = NOW, symbol: str = "BTCUSDT"
) -> Fill:
    return Fill(
        client_order_id="seed",
        symbol=symbol,
        side=Side.BUY,
        price=price,
        quantity=D(quantity),
        fee=D("0"),
        fee_asset="USDT",
        ts_ms=ts,
        is_maker=False,
        trade_id=f"seed-{ts}-{symbol}",
    )


# ---------------------------------------------------------------------------
# Kelly e dimensionamento
# ---------------------------------------------------------------------------


class TestKelly:
    def test_edge_nullo_nessuna_scommessa(self) -> None:
        assert kelly_fraction(0.5, 1.0) == 0.0

    def test_edge_negativo_nessuna_scommessa(self) -> None:
        """Mai scommettere contro se stessi: la frazione e' zero, non negativa."""
        assert kelly_fraction(0.3, 1.0) == 0.0

    def test_formula_nota(self) -> None:
        # p=0.6, b=1 -> f* = 0.6 - 0.4/1 = 0.2
        assert kelly_fraction(0.6, 1.0) == pytest.approx(0.2)
        # p=0.6, b=2 -> f* = 0.6 - 0.4/2 = 0.4
        assert kelly_fraction(0.6, 2.0) == pytest.approx(0.4)

    def test_cresce_con_la_probabilita(self) -> None:
        values = [kelly_fraction(p, 1.5) for p in (0.55, 0.65, 0.75)]
        assert values == sorted(values)

    def test_probabilita_invalida_rifiutata(self) -> None:
        with pytest.raises(ValueError):
            kelly_fraction(1.4, 1.0)


class TestSizing:
    def test_mai_oltre_un_quarto_di_kelly(self, config: AppConfig, filters: SymbolFilters) -> None:
        """Anche con certezza assoluta il sizing resta frazionario."""
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size(
            probability=1.0, payoff_ratio=10.0, price=BTC_PRICE, equity=D("200"), filters=filters
        )
        assert result.kelly_applied <= config.risk.sizing.kelly_fraction
        assert result.notional <= D("200") * D("0.10")

    def test_rispetta_il_tetto_per_ordine(self, config: AppConfig, filters: SymbolFilters) -> None:
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size(
            probability=0.95, payoff_ratio=5.0, price=BTC_PRICE, equity=D("100000"), filters=filters
        )
        assert result.notional <= config.risk.sizing.max_order_quote

    def test_ordine_sotto_il_min_notional_rifiutato_prima_dellinvio(
        self, config: AppConfig, filters: SymbolFilters
    ) -> None:
        """Vincolo operativo: un ordine da 5 sotto il minimo di coppia non parte."""
        big_minimum = SymbolFilters(
            symbol="BTCUSDT",
            tick_size=D("0.01"),
            step_size=D("0.00001"),
            min_quantity=D("0.00001"),
            max_quantity=D("9000"),
            min_notional=D("20"),  # coppia con minimo alto
            base_precision=5,
            quote_precision=2,
            fetched_at_ms=NOW,
        )
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size(
            probability=0.60,
            payoff_ratio=1.2,
            price=BTC_PRICE,
            equity=D("200"),
            filters=big_minimum,
        )
        assert not result.is_tradeable
        assert any("MIN_NOTIONAL" in reason for reason in result.reasons)

    def test_margine_di_sicurezza_sopra_il_min_notional(
        self, config: AppConfig, filters: SymbolFilters
    ) -> None:
        """Un notional che sfiora il minimo viene rifiutato: basta un tick per violarlo."""
        borderline = SymbolFilters(
            symbol="BTCUSDT",
            tick_size=D("0.01"),
            step_size=D("0.00001"),
            min_quantity=D("0.00001"),
            max_quantity=D("9000"),
            min_notional=D("16"),
            base_precision=5,
            quote_precision=2,
            fetched_at_ms=NOW,
        )
        sizer = PositionSizer(config.risk.sizing)
        # equity tale da produrre un notional di ~16.2, sopra il minimo ma sotto 16 * 1.10
        result = sizer.size(
            probability=0.60,
            payoff_ratio=1.5,
            price=BTC_PRICE,
            equity=D("200"),
            filters=borderline,
        )
        assert not result.is_tradeable
        assert any("margine di sicurezza" in reason for reason in result.reasons)

    def test_quantita_troncata_allo_step(self, config: AppConfig) -> None:
        coarse = SymbolFilters(
            symbol="BTCUSDT",
            tick_size=D("0.01"),
            step_size=D("0.001"),
            min_quantity=D("0.001"),
            max_quantity=D("9000"),
            min_notional=D("5"),
            base_precision=3,
            quote_precision=2,
            fetched_at_ms=NOW,
        )
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size(
            probability=0.70, payoff_ratio=1.5, price=D("100"), equity=D("200"), filters=coarse
        )
        assert result.quantity % coarse.step_size == 0

    def test_quantita_azzerata_dallo_step_rifiutata(self, config: AppConfig) -> None:
        """Uno step grossolano puo' azzerare un ordine piccolo: va detto, non subito."""
        coarse = SymbolFilters(
            symbol="BTCUSDT",
            tick_size=D("0.01"),
            step_size=D("1"),
            min_quantity=D("1"),
            max_quantity=D("9000"),
            min_notional=D("5"),
            base_precision=0,
            quote_precision=2,
            fetched_at_ms=NOW,
        )
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size(
            probability=0.70, payoff_ratio=1.5, price=BTC_PRICE, equity=D("200"), filters=coarse
        )
        assert not result.is_tradeable

    def test_chiusura_non_passa_da_kelly(self, config: AppConfig, filters: SymbolFilters) -> None:
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size_to_close(
            quantity_to_close=D("0.001"), price=BTC_PRICE, filters=filters
        )
        assert result.is_tradeable
        assert result.quantity == D("0.001")

    def test_residuo_non_liquidabile_segnalato(
        self, config: AppConfig, filters: SymbolFilters
    ) -> None:
        """Una polvere sotto MIN_NOTIONAL non e' chiudibile: va detto, non ritentato."""
        sizer = PositionSizer(config.risk.sizing)
        result = sizer.size_to_close(
            quantity_to_close=D("0.00002"), price=BTC_PRICE, filters=filters
        )
        assert not result.is_tradeable
        assert any("non liquidabile" in reason for reason in result.reasons)


# ---------------------------------------------------------------------------
# Limiti di portafoglio
# ---------------------------------------------------------------------------


class TestLimits:
    def _checker(
        self,
        config: AppConfig,
        *,
        caps: dict[str, Decimal] | None = None,
        correlations: CorrelationTracker | None = None,
        rates: OrderRateTracker | None = None,
    ) -> LimitChecker:
        limits = {s.symbol: s.max_position_quote for s in config.enabled_symbols}
        limits.update(caps or {})
        return LimitChecker(
            config.risk.limits,
            max_position_quote=limits,
            correlations=correlations,
            rates=rates,
        )

    def test_approva_un_ordine_ragionevole(self, config: AppConfig) -> None:
        checker = self._checker(config)
        portfolio = Portfolio(D("200"))
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.0002"), BTC_PRICE, OrderIntent.OPEN)
        assert checker.check(proposal, snapshot_of(portfolio), now_ms=NOW) == ()

    def test_blocca_oltre_il_numero_massimo_di_posizioni(self, config: AppConfig) -> None:
        checker = self._checker(config, caps={"ADAUSDT": D("50")})
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0003"))
        portfolio.apply_fill(buy_fill("0.01", price=D("3000"), symbol="ETHUSDT"))
        proposal = OrderProposal("ADAUSDT", Side.BUY, D("10"), D("1"), OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(portfolio), now_ms=NOW)
        assert any("posizioni aperte" in r for r in reasons)

    def test_blocca_oltre_lesposizione_lorda(self, config: AppConfig) -> None:
        checker = self._checker(config)
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0008"))  # 48 di esposizione
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.002"), BTC_PRICE, OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(portfolio), now_ms=NOW)
        assert any("esposizione" in r for r in reasons)

    def test_blocca_oltre_il_cap_di_simbolo(self, config: AppConfig) -> None:
        checker = self._checker(config)
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0008"))
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.0001"), BTC_PRICE, OrderIntent.INCREASE)
        reasons = checker.check(proposal, snapshot_of(portfolio), now_ms=NOW)
        assert any("cap di simbolo" in r for r in reasons)

    def test_simbolo_non_configurato_rifiutato(self, config: AppConfig) -> None:
        checker = self._checker(config)
        proposal = OrderProposal("DOGEUSDT", Side.BUY, D("1"), D("1"), OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(Portfolio(D("200"))), now_ms=NOW)
        assert any("non e' fra i simboli configurati" in r for r in reasons)

    def test_vendita_scoperta_rifiutata(self, config: AppConfig) -> None:
        """Sullo spot non si vende cio' che non si possiede."""
        checker = self._checker(config)
        proposal = OrderProposal("BTCUSDT", Side.SELL, D("0.001"), BTC_PRICE, OrderIntent.CLOSE)
        reasons = checker.check(proposal, snapshot_of(Portfolio(D("200"))), now_ms=NOW)
        assert any("scoperto" in r for r in reasons)

    def test_chiusura_consentita_anche_a_limiti_saturi(self, config: AppConfig) -> None:
        """Ridurre il rischio deve restare sempre possibile."""
        checker = self._checker(config)
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0008"))
        portfolio.apply_fill(buy_fill("0.01", price=D("3000"), symbol="ETHUSDT"))
        proposal = OrderProposal("BTCUSDT", Side.SELL, D("0.0008"), BTC_PRICE, OrderIntent.CLOSE)
        assert checker.check(proposal, snapshot_of(portfolio), now_ms=NOW) == ()

    def test_frequenza_ordini_limitata(self, config: AppConfig) -> None:
        rates = OrderRateTracker()
        checker = self._checker(config, rates=rates)
        for i in range(config.risk.limits.max_orders_per_symbol_per_hour):
            rates.record("BTCUSDT", NOW - 3_000_000 + i)
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.0001"), BTC_PRICE, OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(Portfolio(D("200"))), now_ms=NOW)
        assert any("nell'ultima ora" in r for r in reasons)

    def test_intervallo_minimo_fra_ordini(self, config: AppConfig) -> None:
        """Protegge dal bot che entra ed esce su un segnale che oscilla."""
        rates = OrderRateTracker()
        rates.record("BTCUSDT", NOW - 1_000)
        checker = self._checker(config, rates=rates)
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.0001"), BTC_PRICE, OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(Portfolio(D("200"))), now_ms=NOW)
        assert any("minimo" in r for r in reasons)

    def test_ordini_vecchi_escono_dalla_finestra(self, config: AppConfig) -> None:
        rates = OrderRateTracker()
        for i in range(50):
            rates.record("BTCUSDT", NOW - 7_200_000 + i)
        assert rates.count_last_hour("BTCUSDT", NOW) == 0


class TestCorrelation:
    def test_serie_identiche_correlazione_uno(self) -> None:
        tracker = CorrelationTracker(lookback=100, min_samples=10)
        for value in [0.01, -0.02, 0.03, -0.01, 0.02, 0.0, -0.03, 0.01, 0.02, -0.02]:
            tracker.update("A", value)
            tracker.update("B", value)
        rho = tracker.correlation("A", "B")
        assert rho is not None and rho == pytest.approx(1.0)

    def test_serie_opposte_correlazione_meno_uno(self) -> None:
        tracker = CorrelationTracker(lookback=100, min_samples=10)
        for value in [0.01, -0.02, 0.03, -0.01, 0.02, 0.005, -0.03, 0.01, 0.02, -0.02]:
            tracker.update("A", value)
            tracker.update("B", -value)
        rho = tracker.correlation("A", "B")
        assert rho is not None and rho == pytest.approx(-1.0)

    def test_pochi_campioni_non_stimabile(self) -> None:
        tracker = CorrelationTracker(lookback=100, min_samples=30)
        for value in (0.01, 0.02, -0.01):
            tracker.update("A", value)
            tracker.update("B", value)
        assert tracker.correlation("A", "B") is None

    def test_serie_costante_non_stimabile(self) -> None:
        """Varianza nulla: la correlazione e' indefinita, non zero."""
        tracker = CorrelationTracker(lookback=100, min_samples=5)
        for _ in range(10):
            tracker.update("A", 0.0)
            tracker.update("B", 0.01)
        assert tracker.correlation("A", "B") is None

    def test_valori_non_finiti_scartati(self) -> None:
        tracker = CorrelationTracker(lookback=100, min_samples=5)
        tracker.update("A", float("nan"))
        tracker.update("A", float("inf"))
        assert tracker.samples("A") == 0

    def test_posizione_molto_correlata_rifiutata(self, config: AppConfig) -> None:
        """Due posizioni correlate sono una sola posizione di taglia doppia."""
        tracker = CorrelationTracker(
            config.risk.limits.correlation_lookback_bars,
            config.risk.limits.min_correlation_samples,
        )
        for i in range(config.risk.limits.min_correlation_samples + 5):
            value = 0.01 if i % 2 == 0 else -0.01
            tracker.update("BTCUSDT", value)
            tracker.update("ETHUSDT", value * 1.05)
        checker = LimitChecker(
            config.risk.limits,
            max_position_quote={s.symbol: s.max_position_quote for s in config.enabled_symbols},
            correlations=tracker,
        )
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.01", price=D("3000"), symbol="ETHUSDT"))
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.0001"), BTC_PRICE, OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(portfolio), now_ms=NOW)
        assert any("correlazione" in r for r in reasons)

    def test_correlazione_non_stimabile_blocca_lapertura(self, config: AppConfig) -> None:
        """In dubbio non si apre: la mancanza di dati non e' un via libera."""
        checker = LimitChecker(
            config.risk.limits,
            max_position_quote={s.symbol: s.max_position_quote for s in config.enabled_symbols},
        )
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.01", price=D("3000"), symbol="ETHUSDT"))
        proposal = OrderProposal("BTCUSDT", Side.BUY, D("0.0001"), BTC_PRICE, OrderIntent.OPEN)
        reasons = checker.check(proposal, snapshot_of(portfolio), now_ms=NOW)
        assert any("non stimabile" in r for r in reasons)


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_parte_armato(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        assert not switch.is_tripped
        assert switch.allows_opening(NOW)

    def test_scatta_sul_drawdown_giornaliero(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        portfolio = Portfolio(D("200"))
        portfolio.start_new_day("2026-01-01")
        portfolio.cash = D("193")  # -3.5% sulla giornata
        events = switch.check(portfolio.snapshot(NOW), now_ms=NOW)
        assert TriggerReason.DAILY_DRAWDOWN in {e.reason for e in events}
        assert switch.is_tripped

    def test_scatta_sul_drawdown_totale(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        portfolio = Portfolio(D("200"))
        portfolio.cash = D("178")  # -11% dal massimo
        portfolio.start_new_day("2026-01-02")  # il giorno riparte: resta solo il totale
        events = switch.check(portfolio.snapshot(NOW), now_ms=NOW)
        assert TriggerReason.TOTAL_DRAWDOWN in {e.reason for e in events}

    def test_scatta_sulle_perdite_consecutive(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        portfolio = Portfolio(D("1000"))
        for i in range(config.risk.killswitch.max_consecutive_losses):
            portfolio.apply_fill(buy_fill("0.001", price=D("100"), ts=NOW + i * 2))
            portfolio.apply_fill(
                Fill(
                "c", "BTCUSDT", Side.SELL, D("99"), D("0.001"), D("0"), "USDT",
                NOW + i * 2 + 1, False, f"s{i}",
            )
            )
        assert portfolio.consecutive_losses == config.risk.killswitch.max_consecutive_losses
        events = switch.check(portfolio.snapshot(NOW), now_ms=NOW)
        assert TriggerReason.CONSECUTIVE_LOSSES in {e.reason for e in events}

    def test_una_vincita_azzera_la_serie(self) -> None:
        portfolio = Portfolio(D("1000"))
        portfolio.apply_fill(buy_fill("0.001", price=D("100"), ts=NOW))
        portfolio.apply_fill(
            Fill(
                "c", "BTCUSDT", Side.SELL, D("99"), D("0.001"), D("0"), "USDT",
                NOW + 1, False, "s1",
            )
        )
        assert portfolio.consecutive_losses == 1
        portfolio.apply_fill(buy_fill("0.001", price=D("100"), ts=NOW + 2))
        portfolio.apply_fill(
            Fill(
                "c", "BTCUSDT", Side.SELL, D("101"), D("0.001"), D("0"), "USDT",
                NOW + 3, False, "s2",
            )
        )
        assert portfolio.consecutive_losses == 0

    def test_scatta_sulla_latenza(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        events = switch.check(
            Portfolio(D("200")).snapshot(NOW),
            now_ms=NOW,
            last_order_latency_ms=config.risk.killswitch.max_order_latency_ms + 1,
        )
        assert TriggerReason.ORDER_LATENCY in {e.reason for e in events}

    def test_scatta_sul_feed_fermo(self, config: AppConfig) -> None:
        """Decidere su un prezzo vecchio e' peggio che non decidere."""
        switch = KillSwitch(config.risk.killswitch)
        switch.record_data("BTCUSDT", NOW - config.risk.killswitch.max_data_staleness_ms - 1)
        events = switch.check(Portfolio(D("200")).snapshot(NOW), now_ms=NOW)
        assert TriggerReason.DATA_STALENESS in {e.reason for e in events}

    def test_feed_fresco_non_fa_scattare(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        switch.record_data("BTCUSDT", NOW - 100)
        assert switch.check(Portfolio(D("200")).snapshot(NOW), now_ms=NOW) == ()

    def test_scatta_sulla_tempesta_di_riconnessioni(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        for i in range(config.risk.killswitch.max_reconnects_per_hour):
            switch.record_reconnect(NOW - 1_000 * i)
        events = switch.check(Portfolio(D("200")).snapshot(NOW), now_ms=NOW)
        assert TriggerReason.RECONNECT_STORM in {e.reason for e in events}

    def test_riconnessioni_vecchie_non_contano(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        for i in range(50):
            switch.record_reconnect(NOW - 7_200_000 - i)
        assert switch.check(Portfolio(D("200")).snapshot(NOW), now_ms=NOW) == ()

    def test_registra_tutte_le_cause_non_solo_la_prima(self, config: AppConfig) -> None:
        """Per l'autopsia serve sapere quante cose sono andate storte insieme."""
        switch = KillSwitch(config.risk.killswitch)
        switch.record_data("BTCUSDT", NOW - 999_999)
        portfolio = Portfolio(D("200"))
        portfolio.start_new_day("2026-01-01")
        portfolio.cash = D("150")
        events = switch.check(portfolio.snapshot(NOW), now_ms=NOW, last_order_latency_ms=99_999)
        assert len(events) >= 3

    def test_blocca_le_aperture_dopo_lo_scatto(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_manually("test", NOW)
        assert not switch.allows_opening(NOW)

    def test_nessun_riarmo_automatico(self, config: AppConfig) -> None:
        """auto_resume e' falso: dopo uno stop deve guardare un umano."""
        assert not config.risk.killswitch.auto_resume
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_manually("test", NOW)
        much_later = NOW + config.risk.killswitch.cooldown_minutes * 60_000 * 10
        assert not switch.allows_opening(much_later)

    def test_reset_rifiutato_durante_il_cooldown(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_manually("test", NOW)
        assert not switch.reset(NOW + 60_000)
        assert switch.is_tripped

    def test_reset_accettato_dopo_il_cooldown(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_manually("test", NOW)
        after = NOW + config.risk.killswitch.cooldown_minutes * 60_000
        assert switch.reset(after)
        assert not switch.is_tripped

    def test_chiede_la_chiusura_delle_posizioni(self, config: AppConfig) -> None:
        assert config.risk.killswitch.flatten_positions_on_trigger
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_manually("test", NOW)
        assert switch.flatten_pending
        switch.mark_flattened()
        assert not switch.flatten_pending

    def test_incoerenza_di_stato_fa_scattare(self, config: AppConfig) -> None:
        switch = KillSwitch(config.risk.killswitch)
        switch.trip_on_state_mismatch("posizione locale 0.5 contro exchange 0.3", NOW)
        assert switch.is_tripped
        assert switch.events[-1].reason is TriggerReason.STATE_MISMATCH


# ---------------------------------------------------------------------------
# Orchestrazione
# ---------------------------------------------------------------------------


class TestRiskManager:
    def test_approva_un_segnale_buono(self, manager: RiskManager, filters: SymbolFilters) -> None:
        decision = manager.evaluate(
            long_signal(), snapshot_of(Portfolio(D("200"))), context(filters)
        )
        assert decision.approved, decision.rejection_reasons
        assert decision.order is not None
        assert decision.order.side is Side.BUY
        assert decision.order.quantity > 0

    def test_ogni_decisione_porta_con_se_le_feature(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        """Principio 4: senza le feature che l'hanno generata non c'e' autopsia."""
        decision = manager.evaluate(
            long_signal(), snapshot_of(Portfolio(D("200"))), context(filters)
        )
        assert decision.signal is not None
        assert decision.signal.features["ofi_1s"] == 0.42
        assert "equity" in decision.risk_snapshot
        assert "kelly_applied" in decision.risk_snapshot

    def test_rifiuto_sempre_motivato(self, manager: RiskManager, filters: SymbolFilters) -> None:
        decision = manager.evaluate(
            long_signal(probability=0.50), snapshot_of(Portfolio(D("200"))), context(filters)
        )
        assert not decision.approved
        assert decision.rejection_reasons

    def test_probabilita_sotto_soglia_rifiutata(
        self, manager: RiskManager, config: AppConfig, filters: SymbolFilters
    ) -> None:
        below = config.signals.min_probability - 0.01
        decision = manager.evaluate(
            long_signal(probability=below), snapshot_of(Portfolio(D("200"))), context(filters)
        )
        assert not decision.approved
        assert any("probabilita'" in r for r in decision.rejection_reasons)

    def test_edge_che_non_paga_i_costi_rifiutato(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        """Principio 5: un segnale che non copre le fee non e' un segnale."""
        decision = manager.evaluate(
            long_signal(probability=0.56),
            snapshot_of(Portfolio(D("200"))),
            context(filters, estimated_cost_bps=200.0),
        )
        assert not decision.approved
        assert any("edge netto" in r for r in decision.rejection_reasons)

    def test_kill_switch_blocca_le_aperture(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        manager.killswitch.trip_manually("stop di prova", NOW)
        decision = manager.evaluate(
            long_signal(), snapshot_of(Portfolio(D("200"))), context(filters)
        )
        assert not decision.approved
        assert any("kill switch" in r for r in decision.rejection_reasons)

    def test_kill_switch_non_blocca_le_chiusure(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        """Con il kill switch attivo si deve poter ancora uscire."""
        manager.killswitch.trip_manually("stop di prova", NOW)
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0005"))
        exit_signal = Signal(
            symbol="BTCUSDT",
            ts_ms=NOW,
            direction=SignalDirection.FLAT,
            probability=0.5,
            features={},
            generator="rules",
            horizon_ms=300_000,
        )
        decision = manager.evaluate(exit_signal, snapshot_of(portfolio), context(filters))
        assert decision.approved, decision.rejection_reasons
        assert decision.order is not None
        assert decision.order.side is Side.SELL
        assert decision.order.reduce_only

    def test_segnale_short_senza_posizione_non_produce_ordine(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        """Sullo spot una vista ribassista senza posizione non e' eseguibile."""
        short = Signal(
            symbol="BTCUSDT",
            ts_ms=NOW,
            direction=SignalDirection.SHORT,
            probability=0.80,
            features={},
            generator="rules",
            horizon_ms=300_000,
        )
        decision = manager.evaluate(short, snapshot_of(Portfolio(D("200"))), context(filters))
        assert not decision.approved

    def test_filtri_scaduti_bloccano_lordine(
        self, manager: RiskManager, config: AppConfig
    ) -> None:
        """Un MIN_NOTIONAL vecchio di ore non e' piu' una fonte attendibile."""
        stale = SymbolFilters(
            symbol="BTCUSDT",
            tick_size=D("0.01"),
            step_size=D("0.00001"),
            min_quantity=D("0.00001"),
            max_quantity=D("9000"),
            min_notional=D("5"),
            base_precision=5,
            quote_precision=2,
            fetched_at_ms=NOW - config.exchange.exchange_info_refresh_s * 1000 - 1,
        )
        decision = manager.evaluate(
            long_signal(), snapshot_of(Portfolio(D("200"))), context(stale)
        )
        assert not decision.approved
        assert any("exchangeInfo" in r for r in decision.rejection_reasons)

    def test_la_decisione_approvata_consuma_il_rate_limit(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        """Due segnali ravvicinati: il secondo cade sull'intervallo minimo."""
        portfolio = Portfolio(D("200"))
        first = manager.evaluate(long_signal(), snapshot_of(portfolio), context(filters))
        assert first.approved
        second = manager.evaluate(long_signal(), snapshot_of(portfolio), context(filters))
        assert not second.approved
        assert any("minimo" in r for r in second.rejection_reasons)

    def test_le_decisioni_sono_tutte_registrate(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        for probability in (0.50, 0.70, 0.52):
            manager.evaluate(
                long_signal(probability), snapshot_of(Portfolio(D("200"))), context(filters)
            )
        assert len(manager.decisions) == 3

    def test_flatten_genera_ordini_di_chiusura(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0005"))
        orders: list[Order] = manager.flatten_all(
            snapshot_of(portfolio), {"BTCUSDT": BTC_PRICE}, {"BTCUSDT": filters}
        )
        assert len(orders) == 1
        assert orders[0].side is Side.SELL
        assert orders[0].reduce_only
        assert orders[0].quantity == D("0.0005")

    def test_flatten_segnala_le_posizioni_non_liquidabili(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.00002"))  # valore ~1.2, sotto MIN_NOTIONAL
        orders = manager.flatten_all(
            snapshot_of(portfolio), {"BTCUSDT": BTC_PRICE}, {"BTCUSDT": filters}
        )
        assert orders == []
        assert manager.decisions[-1].rejection_reasons

    def test_client_order_id_presente_e_unico(
        self, manager: RiskManager, filters: SymbolFilters
    ) -> None:
        portfolio = Portfolio(D("200"))
        first = manager.evaluate(long_signal(), snapshot_of(portfolio), context(filters))
        manager.clock.advance_by(120_000)  # type: ignore[attr-defined]
        second = manager.evaluate(long_signal(), snapshot_of(portfolio), context(filters))
        assert first.order is not None and second.order is not None
        assert first.order.client_order_id != second.order.client_order_id


# ---------------------------------------------------------------------------
# Contabilita' di portafoglio: alimenta ogni soglia di rischio
# ---------------------------------------------------------------------------


class TestPortfolioAccounting:
    def test_comprare_non_cambia_lequity(self) -> None:
        """Regressione: comprare sposta cash in posizione, non distrugge valore.

        Con equity calcolata male ogni acquisto sembrava una perdita e il kill
        switch scattava sul drawdown al primo ordine.
        """
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.0005"))  # 30 di controvalore
        assert portfolio.cash == D("170")
        assert portfolio.equity == D("200")
        assert portfolio.snapshot(NOW).daily_drawdown_pct == 0.0

    def test_lequity_segue_il_prezzo(self) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.001"))  # 60 di controvalore
        portfolio.update_mark("BTCUSDT", D("66000"))  # +10%
        assert portfolio.equity == D("206")
        assert portfolio.unrealized_pnl == D("6")

    def test_le_fee_riducono_lequity(self) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(
            Fill(
                "c", "BTCUSDT", Side.BUY, BTC_PRICE, D("0.0005"), D("0.03"), "USDT",
                NOW, False, "t",
            )
        )
        assert portfolio.equity == D("199.97")

    def test_il_massimo_storico_non_scende(self) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.001"))
        portfolio.update_mark("BTCUSDT", D("72000"))  # equity 212
        assert portfolio.peak_equity == D("212")
        portfolio.update_mark("BTCUSDT", D("54000"))  # equity 194
        assert portfolio.peak_equity == D("212")
        assert portfolio.snapshot(NOW).total_drawdown_pct == pytest.approx(18 / 212)

    def test_il_nuovo_giorno_azzera_il_riferimento_giornaliero(self) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.start_new_day("2026-01-01")
        portfolio.apply_fill(buy_fill("0.001"))
        portfolio.update_mark("BTCUSDT", D("54000"))  # -6 sulla giornata
        assert portfolio.snapshot(NOW).daily_drawdown_pct == pytest.approx(6 / 200)
        assert portfolio.start_new_day("2026-01-02")
        assert portfolio.snapshot(NOW).daily_drawdown_pct == 0.0
        # il drawdown totale resta: il massimo storico non si azzera a mezzanotte
        assert portfolio.snapshot(NOW).total_drawdown_pct > 0

    def test_lo_stesso_giorno_non_riazzera(self) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.start_new_day("2026-01-01")
        assert not portfolio.start_new_day("2026-01-01")

    def test_vendere_restituisce_cash(self) -> None:
        portfolio = Portfolio(D("200"))
        portfolio.apply_fill(buy_fill("0.001"))
        portfolio.apply_fill(
            Fill(
                "c", "BTCUSDT", Side.SELL, D("66000"), D("0.001"), D("0"), "USDT",
                NOW + 1, False, "s",
            )
        )
        assert portfolio.positions["BTCUSDT"].is_flat
        assert portfolio.cash == D("206")
        assert portfolio.equity == D("206")
        assert portfolio.realized_pnl == D("6")

    def test_capitale_iniziale_non_positivo_rifiutato(self) -> None:
        with pytest.raises(ValueError):
            Portfolio(D("0"))
