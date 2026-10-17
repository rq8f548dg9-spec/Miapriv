"""Test dei generatori di segnale."""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.types import SignalDirection
from features.engine import FeatureVector
from signals.base import TradePlan, logistic
from signals.ml import (
    MLSignalGenerator,
    ModelNotImplementedError,
    expected_calibration_error,
    reliability_curve,
)
from signals.rules import RuleBasedSignalGenerator, RuleWeights

NOW = 1_700_000_000_000


def vector(complete: bool = True, **features: float) -> FeatureVector:
    values: dict[str, float] = {"atr_bps": 20.0, "close": 60000.0}
    values.update(features)
    return FeatureVector(
        symbol="BTCUSDT",
        ts_ms=NOW,
        values=values,
        is_complete=complete,
        bars_seen=300,
        reference_price=Decimal("60000"),
    )


class TestLogistic:
    def test_centro(self) -> None:
        assert logistic(0.0) == pytest.approx(0.5)

    def test_monotona(self) -> None:
        assert logistic(-2) < logistic(0) < logistic(2)

    def test_stabile_agli_estremi(self) -> None:
        assert 0.0 <= logistic(-10_000) <= 1.0
        assert 0.0 <= logistic(10_000) <= 1.0


class TestTradePlan:
    def test_payoff_dal_rapporto(self) -> None:
        plan = TradePlan(stop_bps=20.0, take_profit_bps=30.0)
        assert plan.payoff_ratio == pytest.approx(1.5)
        assert plan.expected_move_bps == pytest.approx(30.0)

    def test_stop_non_positivo_rifiutato(self) -> None:
        """Un piano senza stop non e' un piano."""
        with pytest.raises(ValueError):
            TradePlan(stop_bps=0.0, take_profit_bps=30.0)


class TestRuleBaseline:
    @pytest.fixture
    def generator(self) -> RuleBasedSignalGenerator:
        return RuleBasedSignalGenerator()

    def test_niente_segnali_prima_del_warmup(self, generator: RuleBasedSignalGenerator) -> None:
        assert generator.generate(vector(complete=False), has_position=False, now_ms=NOW) is None

    def test_flusso_in_acquisto_genera_long(self, generator: RuleBasedSignalGenerator) -> None:
        result = generator.generate(
            vector(ofi_1s=0.8, book_imbalance=0.5), has_position=False, now_ms=NOW
        )
        assert result is not None
        assert result.direction is SignalDirection.LONG
        assert result.signal.probability > 0.5

    def test_flusso_in_vendita_nessun_ingresso(self, generator: RuleBasedSignalGenerator) -> None:
        """Sullo spot una vista ribassista senza posizione non e' eseguibile."""
        result = generator.generate(
            vector(ofi_1s=-0.8, book_imbalance=-0.5), has_position=False, now_ms=NOW
        )
        assert result is None

    def test_flusso_in_vendita_chiude_la_posizione(
        self, generator: RuleBasedSignalGenerator
    ) -> None:
        result = generator.generate(
            vector(ofi_1s=-0.8, book_imbalance=-0.5), has_position=True, now_ms=NOW
        )
        assert result is not None
        assert result.direction is SignalDirection.FLAT

    def test_probabilita_cresce_con_il_punteggio(
        self, generator: RuleBasedSignalGenerator
    ) -> None:
        weak = generator.generate(vector(ofi_1s=0.2), has_position=False, now_ms=NOW)
        strong = generator.generate(vector(ofi_1s=0.9), has_position=False, now_ms=NOW)
        assert weak is not None and strong is not None
        assert strong.signal.probability > weak.signal.probability

    def test_probabilita_resta_moderata(self, generator: RuleBasedSignalGenerator) -> None:
        """Una baseline non deve produrre certezze: Kelly le prenderebbe sul serio."""
        result = generator.generate(
            vector(ofi_1s=1.0, book_imbalance=1.0, book_microprice_dev_bps=50.0),
            has_position=False,
            now_ms=NOW,
        )
        assert result is not None
        assert result.signal.probability < 0.90

    def test_mean_reversion_contrasta_il_flusso(
        self, generator: RuleBasedSignalGenerator
    ) -> None:
        with_spike = generator.score(vector(ofi_1s=0.5, ret_zscore=3.0))
        without = generator.score(vector(ofi_1s=0.5, ret_zscore=0.0))
        assert with_spike < without

    def test_senza_atr_nessun_segnale(self, generator: RuleBasedSignalGenerator) -> None:
        """Niente ATR, niente stop: meglio fermarsi che inventare una distanza."""
        values = vector(ofi_1s=0.9).values
        no_atr = FeatureVector(
            symbol="BTCUSDT",
            ts_ms=NOW,
            values={k: v for k, v in values.items() if k != "atr_bps"},
            is_complete=True,
            bars_seen=300,
            reference_price=Decimal("60000"),
        )
        assert generator.generate(no_atr, has_position=False, now_ms=NOW) is None

    def test_stop_proporzionale_alla_volatilita(
        self, generator: RuleBasedSignalGenerator
    ) -> None:
        calm = generator.generate(vector(ofi_1s=0.9, atr_bps=10.0), has_position=False, now_ms=NOW)
        wild = generator.generate(vector(ofi_1s=0.9, atr_bps=80.0), has_position=False, now_ms=NOW)
        assert calm is not None and wild is not None
        assert wild.plan.stop_bps > calm.plan.stop_bps

    def test_stop_minimo_rispettato(self) -> None:
        """Uno stop dentro lo spread verrebbe colpito dal rumore."""
        generator = RuleBasedSignalGenerator(RuleWeights(min_stop_bps=15.0))
        result = generator.generate(vector(ofi_1s=0.9, atr_bps=1.0), has_position=False, now_ms=NOW)
        assert result is not None
        assert result.plan.stop_bps == pytest.approx(15.0)

    def test_il_segnale_porta_con_se_le_feature(
        self, generator: RuleBasedSignalGenerator
    ) -> None:
        result = generator.generate(vector(ofi_1s=0.8), has_position=False, now_ms=NOW)
        assert result is not None
        assert result.signal.features["ofi_1s"] == 0.8
        assert "score" in result.signal.metadata

    def test_contributi_ispezionabili(self, generator: RuleBasedSignalGenerator) -> None:
        """Serve a capire quale effetto ha generato la decisione."""
        contributions = generator.contributions(vector(ofi_1s=0.8, book_imbalance=-0.2))
        assert contributions["order_flow"] > 0
        assert contributions["book_imbalance"] < 0
        assert sum(contributions.values()) == pytest.approx(
            generator.score(vector(ofi_1s=0.8, book_imbalance=-0.2))
        )

    def test_deterministico(self, generator: RuleBasedSignalGenerator) -> None:
        sample = vector(ofi_1s=0.37, book_imbalance=0.11)
        first = generator.generate(sample, has_position=False, now_ms=NOW)
        second = generator.generate(sample, has_position=False, now_ms=NOW)
        assert first is not None and second is not None
        assert first.signal.probability == second.signal.probability

    def test_pesi_invalidi_rifiutati(self) -> None:
        with pytest.raises(ValueError):
            RuleWeights(scale=0.0)


class TestMLPlaceholder:
    def test_rifiuta_di_generare(self) -> None:
        """Il modello si scrive solo dopo che la baseline ha retto i costi."""
        with pytest.raises(ModelNotImplementedError, match="segnaposto"):
            MLSignalGenerator().generate(vector(), has_position=False, now_ms=NOW)

    def test_validate_senza_modello(self) -> None:
        with pytest.raises(ModelNotImplementedError):
            MLSignalGenerator().validate_model()


class TestCalibration:
    def test_modello_perfettamente_calibrato(self) -> None:
        probabilities = [0.1] * 100 + [0.9] * 100
        outcomes = [i < 10 for i in range(100)] + [i < 90 for i in range(100)]
        assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.0, abs=1e-9)

    def test_modello_sovraconfidente_penalizzato(self) -> None:
        """Il modo piu' rapido di farsi male con Kelly."""
        probabilities = [0.95] * 100
        outcomes = [i < 50 for i in range(100)]  # ne azzecca la meta'
        assert expected_calibration_error(probabilities, outcomes) == pytest.approx(0.45)

    def test_curva_di_affidabilita(self) -> None:
        curve = reliability_curve([0.2, 0.25, 0.8, 0.85], [False, True, True, True], bins=5)
        assert len(curve) == 2
        assert curve[0][2] == 2  # due osservazioni nel primo bin popolato

    def test_lunghezze_diverse_rifiutate(self) -> None:
        with pytest.raises(ValueError):
            reliability_curve([0.5], [True, False])

    def test_probabilita_fuori_range_rifiutata(self) -> None:
        with pytest.raises(ValueError):
            reliability_curve([1.5], [True])
