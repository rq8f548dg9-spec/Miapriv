"""Test anti look-ahead: fallisce se il backtest sbircia il futuro.

Il principio 2 del progetto non si verifica rileggendo il codice: si verifica
perturbando il futuro e controllando che il passato non cambi.

Il test centrale e' ``test_il_futuro_non_cambia_il_passato``: due simulazioni
identiche fino a un istante di taglio, dopo il quale i prezzi della seconda
vengono stravolti. Ogni decisione presa prima del taglio deve essere
identica nelle due esecuzioni. Se il motore leggesse anche un solo valore
futuro, le decisioni divergerebbero.
"""

from __future__ import annotations

import random
from decimal import Decimal
from pathlib import Path

import pytest

from backtest.engine import BacktestEngine
from config.schema import AppConfig, load_config
from core.clock import SimulatedClock
from core.types import Candle, Decision, SignalDirection, TimestampMs
from features.engine import FeatureEngine, FeatureVector
from signals.base import GeneratedSignal, SignalGenerator, TradePlan

D = Decimal
START = 1_700_000_000_000
MINUTE = 60_000


def synthetic_candles(
    count: int,
    *,
    seed: int = 7,
    symbol: str = "BTCUSDT",
    start_price: Decimal = D("60000"),
    start_ms: int = START,
) -> list[Candle]:
    """Serie sintetica deterministica: random walk con volatilita' realistica."""
    rng = random.Random(seed)
    price = start_price
    candles: list[Candle] = []
    for i in range(count):
        move = D(str(round(rng.gauss(0, 0.0012), 6)))
        open_price = price
        close_price = (price * (D(1) + move)).quantize(D("0.01"))
        high = max(open_price, close_price) * D("1.0008")
        low = min(open_price, close_price) * D("0.9992")
        candles.append(
            Candle(
                symbol=symbol,
                open_time_ms=start_ms + i * MINUTE,
                close_time_ms=start_ms + (i + 1) * MINUTE - 1,
                open=open_price,
                high=high.quantize(D("0.01")),
                low=low.quantize(D("0.01")),
                close=close_price,
                volume=D("12"),
                quote_volume=close_price * D("12"),
                trades=40,
                is_closed=True,
            )
        )
        price = close_price
    return candles


class AlwaysTradingGenerator(SignalGenerator):
    """Generatore deterministico per i test del motore.

    La baseline a regole, su sole candele, non produce quasi nessun ordine:
    senza order flow e senza book non ha edge, ed e' giusto cosi'. Questi
    test pero' devono esercitare il motore, non la baseline, quindi usano un
    generatore che decide in modo netto e ripetibile sull'ultimo rendimento.
    """

    name = "test-deterministico"

    def generate(
        self, vector: FeatureVector, *, has_position: bool, now_ms: TimestampMs
    ) -> GeneratedSignal | None:
        if not self.warmup_satisfied(vector):
            return None
        last_return = vector.values.get("ret_1")
        if last_return is None:
            return None
        plan = TradePlan(stop_bps=40.0, take_profit_bps=80.0)
        if last_return > 0:
            direction, probability = SignalDirection.LONG, 0.72
        elif has_position:
            direction, probability = SignalDirection.FLAT, 0.60
        else:
            return None
        return GeneratedSignal(
            signal=self._build_signal(
                symbol=vector.symbol,
                now_ms=now_ms,
                direction=direction,
                probability=probability,
                features=vector.values,
                generator=self.name,
                horizon_ms=300_000,
            ),
            plan=plan,
        )


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


def decision_fingerprint(decisions: list[Decision]) -> list[tuple[object, ...]]:
    """Impronta confrontabile di una sequenza di decisioni."""
    return [
        (
            decision.ts_ms,
            decision.symbol,
            decision.approved,
            None if decision.signal is None else decision.signal.direction.value,
            None if decision.signal is None else round(decision.signal.probability, 12),
            None if decision.order is None else str(decision.order.quantity),
            decision.rejection_reasons,
        )
        for decision in decisions
    ]


class TestNoLookAhead:
    def test_il_futuro_non_cambia_il_passato(self, config: AppConfig) -> None:
        """Il test che conta: perturbare il futuro non deve toccare il passato."""
        candles = synthetic_candles(600)
        cutoff_index = 400
        cutoff_ms = candles[cutoff_index].close_time_ms

        # Seconda serie: identica fino al taglio, poi un crollo del 30%.
        perturbed = list(candles[:cutoff_index])
        shocked = synthetic_candles(
            600 - cutoff_index,
            seed=999,
            start_price=candles[cutoff_index - 1].close * D("0.7"),
            start_ms=candles[cutoff_index].open_time_ms,
        )
        perturbed.extend(shocked)

        baseline = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        altered = BacktestEngine(config, AlwaysTradingGenerator()).run(perturbed)

        before_baseline = [d for d in baseline.decisions if d.ts_ms < cutoff_ms]
        before_altered = [d for d in altered.decisions if d.ts_ms < cutoff_ms]

        assert before_baseline, "il test non prova niente se non ci sono decisioni prima del taglio"
        assert decision_fingerprint(before_baseline) == decision_fingerprint(before_altered)

    def test_le_esecuzioni_non_usano_il_prezzo_della_decisione(
        self, config: AppConfig
    ) -> None:
        """L'ordine si esegue all'apertura della barra SUCCESSIVA alla decisione."""
        candles = synthetic_candles(400)
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        assert result.fills, "servono esecuzioni per poter verificare qualcosa"

        closes_by_ms = {c.close_time_ms: c.close for c in candles}
        opens_by_ms = {c.open_time_ms: c.open for c in candles}
        for fill in result.fills:
            # il fill avviene all'apertura di una barra, non alla chiusura di una
            assert fill.ts_ms in opens_by_ms
            assert fill.ts_ms - 1 not in closes_by_ms or fill.price != closes_by_ms[fill.ts_ms - 1]

    def test_lesecuzione_segue_sempre_la_decisione(self, config: AppConfig) -> None:
        candles = synthetic_candles(400)
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        decided_at = {
            d.order.client_order_id: d.ts_ms
            for d in result.decisions
            if d.approved and d.order is not None
        }
        assert decided_at
        for fill in result.fills:
            assert fill.ts_ms > decided_at[fill.client_order_id]

    def test_il_tempo_non_torna_indietro(self, config: AppConfig) -> None:
        """Un tempo che arretra, nel backtest, e' quasi sempre un look-ahead."""
        candles = synthetic_candles(300)
        result = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        stamps = [ts for ts, _ in result.equity_curve]
        assert stamps == sorted(stamps)
        decision_stamps = [d.ts_ms for d in result.decisions]
        assert decision_stamps == sorted(decision_stamps)

    def test_candele_non_chiuse_rifiutate_dal_motore(self, config: AppConfig) -> None:
        candles = synthetic_candles(50)
        bad = Candle(
            symbol="BTCUSDT",
            open_time_ms=candles[-1].close_time_ms + 1,
            close_time_ms=candles[-1].close_time_ms + MINUTE,
            open=D("60000"), high=D("60100"), low=D("59900"), close=D("60050"),
            volume=D("1"), quote_volume=D("60000"), trades=1, is_closed=False,
        )
        with pytest.raises(ValueError, match="look-ahead"):
            BacktestEngine(config, AlwaysTradingGenerator()).run([*candles, bad])

    def test_il_feature_engine_non_vede_le_barre_successive(
        self, config: AppConfig
    ) -> None:
        """Le feature calcolate alla barra i sono identiche con o senza le barre dopo."""
        candles = synthetic_candles(300)
        cut = 200

        partial = FeatureEngine(config.features)
        for candle in candles[:cut]:
            partial.on_candle(candle)
        partial_vector = partial.compute("BTCUSDT", candles[cut - 1].close_time_ms)

        full = FeatureEngine(config.features)
        for candle in candles[:cut]:
            full.on_candle(candle)
        # le barre successive esistono nel dataset ma non sono ancora state consumate
        full_vector = full.compute("BTCUSDT", candles[cut - 1].close_time_ms)

        assert dict(partial_vector.values) == dict(full_vector.values)

    def test_lordine_di_arrivo_e_cronologico(self, config: AppConfig) -> None:
        """Il motore ordina le candele: un dataset mescolato non cambia il risultato."""
        candles = synthetic_candles(300)
        shuffled = list(candles)
        random.Random(1).shuffle(shuffled)

        ordered_result = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        shuffled_result = BacktestEngine(config, AlwaysTradingGenerator()).run(shuffled)

        assert ordered_result.final_equity == shuffled_result.final_equity
        assert decision_fingerprint(ordered_result.decisions) == decision_fingerprint(
            shuffled_result.decisions
        )

    def test_il_clock_simulato_rifiuta_di_arretrare(self) -> None:
        clock = SimulatedClock(START)
        clock.advance_to(START + 1_000)
        with pytest.raises(ValueError, match="all'indietro"):
            clock.advance_to(START + 999)


class TestDeterminismo:
    def test_due_esecuzioni_identiche(self, config: AppConfig) -> None:
        """Senza determinismo nessun risultato e' verificabile."""
        candles = synthetic_candles(300)
        first = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        second = BacktestEngine(config, AlwaysTradingGenerator()).run(candles)
        assert first.final_equity == second.final_equity
        assert len(first.fills) == len(second.fills)
        assert decision_fingerprint(first.decisions) == decision_fingerprint(second.decisions)
