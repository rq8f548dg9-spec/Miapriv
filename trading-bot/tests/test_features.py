"""Test del feature engine: correttezza dei calcoli e assenza di look-ahead."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from config.schema import FeaturesConfig
from core.types import BookLevel, Candle, OrderBookSnapshot, Trade
from features.engine import FeatureEngine
from features.microstructure import OrderFlowImbalance, book_features
from features.momentum import MomentumFeatures
from features.volatility import AverageTrueRange, RealizedVolatility

D = Decimal
NOW = 1_700_000_000_000
MINUTE = 60_000


def candle(close: str, index: int = 0, high: str | None = None, low: str | None = None,
           symbol: str = "BTCUSDT", is_closed: bool = True) -> Candle:
    value = D(close)
    return Candle(
        symbol=symbol,
        open_time_ms=NOW + index * MINUTE,
        close_time_ms=NOW + (index + 1) * MINUTE - 1,
        open=value,
        high=D(high) if high else value * D("1.001"),
        low=D(low) if low else value * D("0.999"),
        close=value,
        volume=D("10"),
        quote_volume=value * D("10"),
        trades=20,
        is_closed=is_closed,
    )


class TestRealizedVolatility:
    def test_nessun_valore_senza_dati(self) -> None:
        """Una stima su tre osservazioni e' peggio di nessuna stima."""
        vol = RealizedVolatility((5, 10))
        for price in ("100", "101", "102"):
            vol.update(D(price))
        assert vol.value(5) is None
        assert vol.features() == {}

    def test_prezzo_costante_volatilita_nulla(self) -> None:
        vol = RealizedVolatility((5,))
        for _ in range(10):
            vol.update(D("100"))
        assert vol.value(5) == pytest.approx(0.0)

    def test_volatilita_cresce_con_le_oscillazioni(self) -> None:
        calm = RealizedVolatility((10,))
        wild = RealizedVolatility((10,))
        for i in range(20):
            calm.update(D("100") + D(i % 2) / 100)
            wild.update(D("100") + D(i % 2) * 5)
        calm_value, wild_value = calm.value(10), wild.value(10)
        assert calm_value is not None and wild_value is not None
        assert wild_value > calm_value * 10

    def test_finestra_non_configurata_rifiutata(self) -> None:
        with pytest.raises(ValueError, match="non configurata"):
            RealizedVolatility((10,)).value(20)

    def test_prezzo_non_positivo_rifiutato(self) -> None:
        with pytest.raises(ValueError):
            RealizedVolatility((5,)).update(D("0"))


class TestATR:
    def test_nessun_valore_prima_del_warmup(self) -> None:
        atr = AverageTrueRange(14)
        for i in range(13):
            atr.update(candle("100", i))
        assert atr.value is None

    def test_valore_dopo_il_warmup(self) -> None:
        atr = AverageTrueRange(5)
        for i in range(5):
            atr.update(candle("100", i, high="102", low="98"))
        assert atr.value == pytest.approx(4.0)

    def test_tiene_conto_dei_gap(self) -> None:
        """Uno stop viene colpito dal range, gap inclusi, non dalla sola barra."""
        with_gap = AverageTrueRange(2)
        with_gap.update(candle("100", 0, high="101", low="99"))
        with_gap.update(candle("130", 1, high="131", low="129"))
        assert with_gap.value is not None
        assert with_gap.value > 2.0  # il salto da 100 a 129 entra nel true range

    def test_valore_in_bps(self) -> None:
        atr = AverageTrueRange(2)
        atr.update(candle("100", 0, high="101", low="99"))
        atr.update(candle("100", 1, high="101", low="99"))
        assert atr.value_bps(D("100")) == pytest.approx(200.0)

    def test_finestra_troppo_corta_rifiutata(self) -> None:
        with pytest.raises(ValueError):
            AverageTrueRange(1)


class TestMomentum:
    def test_rendimento_su_finestra(self) -> None:
        momentum = MomentumFeatures((1, 3), zscore_window=10)
        for price in ("100", "101", "102", "103"):
            momentum.update(D(price))
        assert momentum.ret(1) == pytest.approx(103 / 102 - 1)
        assert momentum.ret(3) == pytest.approx(103 / 100 - 1)

    def test_nessun_rendimento_senza_storia(self) -> None:
        momentum = MomentumFeatures((5,), zscore_window=10)
        momentum.update(D("100"))
        assert momentum.ret(5) is None

    def test_zscore_di_un_movimento_anomalo(self) -> None:
        momentum = MomentumFeatures((1,), zscore_window=20)
        price = D("100")
        for _ in range(20):
            price *= D("1.0001")
            momentum.update(price)
        price *= D("1.05")  # salto fuori scala
        momentum.update(price)
        zscore = momentum.zscore()
        assert zscore is not None and zscore > 3

    def test_zscore_indefinito_su_prezzo_fermo(self) -> None:
        """Varianza nulla: lo z-score non e' zero, e' indefinito."""
        momentum = MomentumFeatures((1,), zscore_window=10)
        for _ in range(15):
            momentum.update(D("100"))
        assert momentum.zscore() is None


class TestOrderFlow:
    def _trade(self, quantity: str, buyer_maker: bool, ts: int) -> Trade:
        return Trade("BTCUSDT", ts, D("100"), D(quantity), ts, buyer_maker)

    def test_tutto_in_acquisto_ofi_positivo(self) -> None:
        flow = OrderFlowImbalance((1_000,))
        for i in range(5):
            flow.update(self._trade("1", buyer_maker=False, ts=NOW + i))
        assert flow.value(1_000, NOW + 10) == pytest.approx(1.0)

    def test_tutto_in_vendita_ofi_negativo(self) -> None:
        flow = OrderFlowImbalance((1_000,))
        for i in range(5):
            flow.update(self._trade("1", buyer_maker=True, ts=NOW + i))
        assert flow.value(1_000, NOW + 10) == pytest.approx(-1.0)

    def test_flusso_bilanciato(self) -> None:
        flow = OrderFlowImbalance((1_000,))
        flow.update(self._trade("1", buyer_maker=False, ts=NOW))
        flow.update(self._trade("1", buyer_maker=True, ts=NOW + 1))
        assert flow.value(1_000, NOW + 10) == pytest.approx(0.0)

    def test_mercato_fermo_non_ha_sbilanciamento(self) -> None:
        """Nessun trade nella finestra: None, non zero."""
        flow = OrderFlowImbalance((1_000,))
        flow.update(self._trade("1", buyer_maker=False, ts=NOW))
        assert flow.value(1_000, NOW + 50_000) is None

    def test_i_trade_futuri_sono_invisibili(self) -> None:
        """Anti look-ahead: un trade gia' nel buffer ma successivo a T non conta."""
        flow = OrderFlowImbalance((10_000,))
        flow.update(self._trade("1", buyer_maker=False, ts=NOW))
        flow.update(self._trade("100", buyer_maker=True, ts=NOW + 5_000))  # futuro
        assert flow.value(10_000, NOW + 1_000) == pytest.approx(1.0)

    def test_finestre_diverse_valori_diversi(self) -> None:
        flow = OrderFlowImbalance((1_000, 30_000))
        flow.update(self._trade("10", buyer_maker=True, ts=NOW))
        flow.update(self._trade("1", buyer_maker=False, ts=NOW + 29_500))
        short = flow.value(1_000, NOW + 29_900)
        long = flow.value(30_000, NOW + 29_900)
        assert short == pytest.approx(1.0)
        assert long is not None and long < 0


class TestBookFeatures:
    def _book(self, bid_qty: str = "1", ask_qty: str = "1") -> OrderBookSnapshot:
        return OrderBookSnapshot(
            symbol="BTCUSDT",
            last_update_id=1,
            bids=(BookLevel(D("100"), D(bid_qty)), BookLevel(D("99"), D("5"))),
            asks=(BookLevel(D("101"), D(ask_qty)), BookLevel(D("102"), D("5"))),
            ts_ms=NOW,
        )

    def test_book_assente_nessuna_feature(self) -> None:
        assert book_features(None, 10) == {}

    def test_book_incrociato_nessuna_feature(self) -> None:
        """Un book incrociato e' una ricostruzione sbagliata, non un segnale."""
        crossed = OrderBookSnapshot(
            "BTCUSDT", 1, (BookLevel(D("103"), D("1")),), (BookLevel(D("102"), D("1")),), NOW
        )
        assert book_features(crossed, 10) == {}

    def test_spread_in_bps(self) -> None:
        features = book_features(self._book(), 10)
        assert features["book_spread_bps"] == pytest.approx(1 / 100.5 * 10_000)

    def test_imbalance_segue_il_lato_piu_pesante(self) -> None:
        heavy_bid = book_features(self._book(bid_qty="10"), 10)
        assert heavy_bid["book_top_imbalance"] > 0
        heavy_ask = book_features(self._book(ask_qty="10"), 10)
        assert heavy_ask["book_top_imbalance"] < 0

    def test_microprice_sopra_il_mid_con_domanda(self) -> None:
        features = book_features(self._book(bid_qty="10"), 10)
        assert features["book_microprice_dev_bps"] > 0


def book_snapshot(ts_ms: int, symbol: str = "BTCUSDT") -> OrderBookSnapshot:
    """Book minimo ma valido, con l'istante dell'ultimo aggiornamento."""
    return OrderBookSnapshot(
        symbol=symbol,
        last_update_id=1,
        bids=(BookLevel(D("99.99"), D("2")), BookLevel(D("99.98"), D("3"))),
        asks=(BookLevel(D("100.01"), D("2")), BookLevel(D("100.02"), D("3"))),
        ts_ms=ts_ms,
    )


class TestFeatureEngine:
    @pytest.fixture
    def config(self) -> FeaturesConfig:
        return FeaturesConfig(
            returns_windows_bars=(1, 5),
            volatility_windows_bars=(5, 10),
            atr_window_bars=5,
            zscore_window_bars=10,
            ofi_windows_ms=(1_000, 5_000),
            book_depth_levels=5,
        )

    def test_warmup_dalla_finestra_piu_lunga(self, config: FeaturesConfig) -> None:
        assert FeatureEngine(config).warmup_bars == 10

    def test_vettore_incompleto_prima_del_warmup(self, config: FeaturesConfig) -> None:
        """Prima del warmup il bot non deve operare."""
        engine = FeatureEngine(config)
        for i in range(3):
            engine.on_candle(candle("100", i))
        vector = engine.compute("BTCUSDT", NOW + 3 * MINUTE)
        assert not vector.is_complete

    def test_vettore_completo_dopo_il_warmup(self, config: FeaturesConfig) -> None:
        engine = FeatureEngine(config)
        price = D("100")
        for i in range(15):
            price *= D("1.001")
            engine.on_candle(candle(str(price), i))
        vector = engine.compute("BTCUSDT", NOW + 15 * MINUTE)
        assert vector.is_complete
        assert "vol_10" in vector.values
        assert "atr_bps" in vector.values
        assert "ret_5_bps" in vector.values

    def test_candela_aperta_rifiutata(self, config: FeaturesConfig) -> None:
        """Il cuore del principio 2: una candela in formazione contiene il futuro."""
        engine = FeatureEngine(config)
        with pytest.raises(ValueError, match="look-ahead"):
            engine.on_candle(candle("100", 0, is_closed=False))

    def test_candela_fuori_ordine_rifiutata(self, config: FeaturesConfig) -> None:
        engine = FeatureEngine(config)
        engine.on_candle(candle("100", 5))
        with pytest.raises(ValueError, match="fuori ordine"):
            engine.on_candle(candle("100", 2))

    def test_la_stessa_candela_due_volte_conta_una(
        self, config: FeaturesConfig
    ) -> None:
        """Una riconnessione puo' riconsegnare l'ultima candela. Contarla due
        volte sposta il warmup, i rendimenti e la volatilita' senza che sia
        arrivato nessun dato nuovo."""
        engine = FeatureEngine(config)
        engine.on_candle(candle("100", 5))
        engine.on_candle(candle("100", 5))
        assert engine.state_for("BTCUSDT").bars_seen == 1

    def test_un_buco_nelle_candele_fa_ripartire_le_finestre(
        self, config: FeaturesConfig
    ) -> None:
        """Le finestre contano barre, non tempo: se fra una barra e l'altra
        mancano ore, rendimenti, volatilita' e ATR mescolano epoche diverse
        e il vettore sembra completo quando non lo e'."""
        engine = FeatureEngine(config)
        for i in range(config.warmup_bars + 5):
            engine.on_candle(candle("100", i))
        assert engine.compute("BTCUSDT", NOW + 1_000 * MINUTE).is_complete

        # salto di mille minuti: manutenzione dell'exchange, feed caduto,
        # storico con una lacuna
        engine.on_candle(candle("100", config.warmup_bars + 1_005))
        stato = engine.state_for("BTCUSDT")
        assert stato.bars_seen == 1, "dopo un buco le finestre ripartono"
        assert stato.gaps_seen == 1
        assert not engine.compute(
            "BTCUSDT", NOW + (config.warmup_bars + 1_006) * MINUTE
        ).is_complete

    def test_le_candele_contigue_non_fanno_ripartire_niente(
        self, config: FeaturesConfig
    ) -> None:
        engine = FeatureEngine(config)
        for i in range(20):
            engine.on_candle(candle("100", i))
        assert engine.state_for("BTCUSDT").bars_seen == 20
        assert engine.state_for("BTCUSDT").gaps_seen == 0

    def test_un_book_vecchio_non_entra_nelle_feature(
        self, config: FeaturesConfig
    ) -> None:
        """Il book pesa per quasi meta' del punteggio delle regole. Se il
        flusso depth si ferma mentre i trade continuano, il bot decide su
        uno spessore di libro che non esiste piu'."""
        engine = FeatureEngine(config)
        for i in range(config.warmup_bars + 1):
            engine.on_candle(candle("100", i))
        engine.on_book("BTCUSDT", book_snapshot(NOW))

        fresco = engine.compute("BTCUSDT", NOW + 1_000)
        assert "book_imbalance" in fresco.values
        assert fresco.values["book_age_ms"] == 1_000

        stantio = engine.compute("BTCUSDT", NOW + config.max_book_age_ms + 1)
        assert "book_imbalance" not in stantio.values, (
            "un book fermo non deve piu' pilotare il segnale"
        )
        assert "book_microprice_dev_bps" not in stantio.values
        assert stantio.values["book_age_ms"] > config.max_book_age_ms

    def test_simboli_indipendenti(self, config: FeaturesConfig) -> None:
        engine = FeatureEngine(config)
        for i in range(15):
            engine.on_candle(candle("100", i, symbol="BTCUSDT"))
        engine.on_candle(candle("3000", 0, symbol="ETHUSDT"))
        assert engine.compute("BTCUSDT", NOW + 15 * MINUTE).is_complete
        assert not engine.compute("ETHUSDT", NOW + MINUTE).is_complete

    def test_eta_della_candela_esposta(self, config: FeaturesConfig) -> None:
        """Serve a capire se si sta decidendo su un dato vecchio."""
        engine = FeatureEngine(config)
        engine.on_candle(candle("100", 0))
        vector = engine.compute("BTCUSDT", NOW + 10 * MINUTE)
        assert vector.values["candle_age_ms"] > 500_000

    def test_le_feature_di_book_entrano_nel_vettore(self, config: FeaturesConfig) -> None:
        engine = FeatureEngine(config)
        for i in range(15):
            engine.on_candle(candle("100", i))
        istante = NOW + 15 * MINUTE
        engine.on_book(
            "BTCUSDT",
            OrderBookSnapshot(
                "BTCUSDT",
                1,
                (BookLevel(D("100"), D("2")),),
                (BookLevel(D("101"), D("1")),),
                istante,
            ),
        )
        vector = engine.compute("BTCUSDT", istante)
        assert "book_spread_bps" in vector.values
        assert vector.values["book_top_imbalance"] > 0

    def test_book_non_allineato_rimuove_le_feature(self, config: FeaturesConfig) -> None:
        """Dopo un buco di sequenza il book e' None e le sue feature spariscono."""
        engine = FeatureEngine(config)
        engine.on_book(
            "BTCUSDT",
            OrderBookSnapshot(
                "BTCUSDT", 1, (BookLevel(D("100"), D("2")),), (BookLevel(D("101"), D("1")),), NOW
            ),
        )
        assert "book_spread_bps" in engine.compute("BTCUSDT", NOW).values
        engine.on_book("BTCUSDT", None)
        assert "book_spread_bps" not in engine.compute("BTCUSDT", NOW).values

    def test_rendimento_di_barra_per_la_correlazione(self, config: FeaturesConfig) -> None:
        engine = FeatureEngine(config)
        engine.on_candle(candle("100", 0))
        engine.on_candle(candle("110", 1))
        assert engine.bar_return("BTCUSDT") == pytest.approx(0.1)

    def test_le_feature_sono_finite(self, config: FeaturesConfig) -> None:
        """Un NaN che arriva al modello produce una decisione senza senso."""
        engine = FeatureEngine(config)
        price = D("100")
        for i in range(20):
            price *= D("1.002")
            engine.on_candle(candle(str(price), i))
            engine.on_trade(Trade("BTCUSDT", i, price, D("1"), NOW + i * MINUTE, i % 2 == 0))
        vector = engine.compute("BTCUSDT", NOW + 20 * MINUTE)
        assert all(math.isfinite(value) for value in vector.values.values())


class TestCapienzaDelFlussoOrdini:
    """``data.buffer_capacity`` era dichiarato e mai letto: il buffer dei
    trade cresceva solo in base al tempo. In una raffica (listing, notizia,
    liquidazioni a catena) quel "solo in base al tempo" sono centinaia di
    migliaia di trade in trenta secondi, per simbolo.

    Il tetto da solo non basta pero': buttare via i trade piu' vecchi e
    continuare a pubblicare l'OFI su una finestra che non si ha piu'
    intera significa pubblicare un numero sbagliato. Meglio dichiarare che
    quella finestra non si sa."""

    def test_il_buffer_non_cresce_oltre_la_capienza(self) -> None:
        flow = OrderFlowImbalance((1_000, 30_000), capacity=10)
        for i in range(100):
            flow.update(
                Trade("BTCUSDT", i, D("100"), D("1"), NOW + i, i % 2 == 0)
            )
        assert flow.size <= 10

    def test_una_finestra_non_piu_coperta_vale_none(self) -> None:
        flow = OrderFlowImbalance((1_000, 30_000), capacity=10)
        for i in range(100):
            flow.update(
                Trade("BTCUSDT", i, D("100"), D("1"), NOW + i, i % 2 == 0)
            )
        now = NOW + 99
        # I dieci trade rimasti coprono NOW+90..NOW+99: una finestra che
        # sta dentro quell'intervallo e' ancora intera, una che comincia
        # prima no.
        assert flow.value(5, now) is not None, "la finestra corta e' ancora intera"
        assert flow.value(30_000, now) is None, (
            "la finestra lunga non e' piu' coperta: dichiararla, non stimarla"
        )
