"""Modello predittivo: addestramento, calibrazione, collegamento.

Il rischio di questo pezzo non e' sbagliare la matematica, e' crederci.
Kelly dimensiona in base alla probabilita' che il modello dichiara: se il
modello dice 0,70 e la verita' e' 0,52, il bot non sta sbagliando di un
po', sta scommettendo molto piu' di quanto quel vantaggio meriti.

Per questo il modello entra nel sistema solo dopo tre cose: un'etichetta
che tiene conto dei costi, una calibrazione misurata fuori campione, e
una soglia oltre la quale si rifiuta di operare.
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path

import pytest

from apprendimento.dataset import Campione, costruisci_dataset
from config.schema import AppConfig, load_config
from signals.logistic import ModelloLogistico, addestra
from tests.test_no_lookahead import synthetic_candles

D = Decimal


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


class TestDataset:
    def test_letichetta_tiene_conto_dei_costi(self, config: AppConfig) -> None:
        """Un rialzo che non copre le commissioni non e' un'occasione: se
        l'etichetta dice di si', il modello impara a perdere."""
        candele = synthetic_candles(500)
        caro = costruisci_dataset(
            config, candele, orizzonte_barre=5, costo_bps=1_000.0
        )
        gratis = costruisci_dataset(
            config, candele, orizzonte_barre=5, costo_bps=0.0
        )
        assert sum(c.etichetta for c in caro) < sum(c.etichetta for c in gratis)

    def test_le_feature_non_contengono_il_futuro(self, config: AppConfig) -> None:
        """Il campione all'istante t deve essere identico anche se dopo t
        il prezzo fa qualunque cosa."""
        candele = synthetic_candles(400)
        taglio = candele[200].close_time_ms

        normale = costruisci_dataset(config, candele, orizzonte_barre=3)
        stravolte = [
            c
            if c.close_time_ms <= taglio
            else type(c)(
                symbol=c.symbol,
                open_time_ms=c.open_time_ms,
                close_time_ms=c.close_time_ms,
                open=c.open * D("3"),
                high=c.high * D("3"),
                low=c.low * D("3"),
                close=c.close * D("3"),
                volume=c.volume,
                quote_volume=c.quote_volume,
                trades=c.trades,
                is_closed=True,
            )
            for c in candele
        ]
        alterato = costruisci_dataset(config, stravolte, orizzonte_barre=3)

        prima_normale = [c for c in normale if c.ts_ms < taglio - 3 * 60_000]
        prima_alterato = [c for c in alterato if c.ts_ms < taglio - 3 * 60_000]
        assert len(prima_normale) == len(prima_alterato)
        for a, b in zip(prima_normale, prima_alterato, strict=True):
            assert a.valori == b.valori

    def test_le_ultime_barre_non_hanno_etichetta(self, config: AppConfig) -> None:
        """Il futuro delle ultime barre non e' ancora successo: quei
        campioni non esistono, non valgono zero."""
        candele = synthetic_candles(300)
        campioni = costruisci_dataset(config, candele, orizzonte_barre=10)
        ultimo_possibile = candele[-11].close_time_ms
        assert max(c.ts_ms for c in campioni) <= ultimo_possibile


class TestAddestramento:
    def _separabile(self, quanti: int = 400) -> list[Campione]:
        """Dati in cui la prima feature decide: se il modello non impara
        questo, non impara niente."""
        campioni = []
        for i in range(quanti):
            x = (i % 20) / 10.0 - 1.0
            campioni.append(
                Campione(
                    symbol="BTCUSDT",
                    ts_ms=1_700_000_000_000 + i * 60_000,
                    valori={"segnale": x, "rumore": ((i * 37) % 11) / 11.0},
                    etichetta=1 if x > 0 else 0,
                )
            )
        return campioni

    def test_impara_una_relazione_che_c_e(self) -> None:
        modello = addestra(self._separabile(), feature=["segnale", "rumore"], passi=600)
        assert modello.predict_proba([1.0, 0.5]) > 0.7
        assert modello.predict_proba([-1.0, 0.5]) < 0.3

    def test_su_rumore_puro_resta_vicino_alla_moneta(self) -> None:
        """Il comportamento che conta davvero: quando non c'e' segnale, il
        modello deve dirlo, non inventare convinzione."""
        campioni = [
            Campione(
                symbol="BTCUSDT",
                ts_ms=i,
                valori={"a": ((i * 7919) % 1000) / 500.0 - 1.0},
                etichetta=i % 2,
            )
            for i in range(400)
        ]
        modello = addestra(campioni, feature=["a"], passi=400)
        probabilita = [modello.predict_proba([x / 10]) for x in range(-10, 11)]
        assert all(0.35 < p < 0.65 for p in probabilita), probabilita

    def test_senza_campioni_non_si_addestra(self) -> None:
        with pytest.raises(ValueError, match="campione"):
            addestra([], feature=["a"])

    def test_con_una_sola_classe_non_si_addestra(self) -> None:
        """Tutti uno o tutti zero: non c'e' niente da separare, e un
        modello addestrato cosi' direbbe sempre la stessa cosa."""
        campioni = [
            Campione(symbol="X", ts_ms=i, valori={"a": float(i)}, etichetta=1)
            for i in range(50)
        ]
        with pytest.raises(ValueError, match="class"):
            addestra(campioni, feature=["a"])


class TestCalibrazione:
    def test_la_calibrazione_si_misura_fuori_campione(self) -> None:
        from signals.ml import expected_calibration_error, reliability_curve

        campioni = [
            Campione(
                symbol="X",
                ts_ms=i,
                valori={"a": (i % 20) / 10.0 - 1.0},
                etichetta=1 if (i % 20) / 10.0 - 1.0 > 0 else 0,
            )
            for i in range(600)
        ]
        meta = len(campioni) // 2
        modello = addestra(campioni[:meta], feature=["a"], passi=600)
        previste = [modello.predict_proba([c.valori["a"]]) for c in campioni[meta:]]
        osservate = [c.etichetta for c in campioni[meta:]]

        curva = reliability_curve(previste, [bool(x) for x in osservate], bins=5)
        assert curva
        errore = expected_calibration_error(
            previste, [bool(x) for x in osservate], bins=5
        )
        assert 0.0 <= errore < 0.25

    def test_il_modello_si_salva_e_si_rilegge(self, tmp_path: Path) -> None:
        campioni = [
            Campione(
                symbol="X", ts_ms=i, valori={"a": float(i % 7) - 3}, etichetta=int(i % 7 > 3)
            )
            for i in range(200)
        ]
        modello = addestra(campioni, feature=["a"], passi=300)
        percorso = tmp_path / "modello.json"
        modello.salva(percorso, errore_calibrazione=0.03, campioni=200)

        riletto = ModelloLogistico.carica(percorso)
        assert riletto.feature_names == modello.feature_names
        assert math.isclose(
            riletto.predict_proba([1.0]), modello.predict_proba([1.0]), rel_tol=1e-9
        )
        assert riletto.errore_calibrazione == 0.03


class TestCollegamento:
    def test_un_modello_scalibrato_non_entra_nel_sistema(self, tmp_path: Path) -> None:
        """E' la guardia che protegge Kelly: una probabilita' sbagliata
        non e' un errore piccolo, e' una scommessa sbagliata di molto."""
        from signals.ml import MLSignalGenerator

        campioni = [
            Campione(
                symbol="X", ts_ms=i, valori={"a": float(i % 7) - 3}, etichetta=int(i % 7 > 3)
            )
            for i in range(200)
        ]
        modello = addestra(campioni, feature=["a"], passi=200)
        generatore = MLSignalGenerator(
            model=modello, measured_calibration_error=0.42, max_calibration_error=0.05
        )
        with pytest.raises(ValueError, match="calibrazione"):
            generatore.validate_model()

    def test_un_modello_calibrato_genera_segnali(self, config: AppConfig) -> None:
        from features.engine import FeatureVector
        from signals.ml import MLSignalGenerator

        campioni = [
            Campione(
                symbol="BTCUSDT",
                ts_ms=i,
                valori={"ret_1_bps": float(i % 21) - 10, "atr_bps": 30.0},
                etichetta=int((i % 21) - 10 > 0),
            )
            for i in range(400)
        ]
        modello = addestra(campioni, feature=["ret_1_bps", "atr_bps"], passi=500)
        generatore = MLSignalGenerator(
            model=modello, measured_calibration_error=0.02, horizon_ms=300_000
        )
        generatore.validate_model()

        vettore = FeatureVector(
            symbol="BTCUSDT",
            ts_ms=1_700_000_000_000,
            values={"ret_1_bps": 9.0, "atr_bps": 30.0, "bars_seen": 300.0},
            is_complete=True,
            bars_seen=300,
            reference_price=D("60000"),
        )
        generato = generatore.generate(vettore, has_position=False, now_ms=vettore.ts_ms)
        assert generato is not None
        assert generato.signal.probability > 0.5
        assert generato.signal.generator == "ml"
        assert generato.plan.stop_bps > 0

    def test_senza_atr_non_genera(self, config: AppConfig) -> None:
        """Senza ATR non c'e' uno stop sensato, e senza stop non c'e'
        piano: lo stesso vincolo della baseline a regole."""
        from features.engine import FeatureVector
        from signals.ml import MLSignalGenerator

        campioni = [
            Campione(
                symbol="BTCUSDT", ts_ms=i, valori={"ret_1_bps": float(i % 5) - 2},
                etichetta=int((i % 5) - 2 > 0),
            )
            for i in range(200)
        ]
        modello = addestra(campioni, feature=["ret_1_bps"], passi=200)
        generatore = MLSignalGenerator(model=modello, measured_calibration_error=0.01)
        vettore = FeatureVector(
            symbol="BTCUSDT",
            ts_ms=1,
            values={"ret_1_bps": 2.0, "bars_seen": 300.0},
            is_complete=True,
            bars_seen=300,
            reference_price=D("60000"),
        )
        assert generatore.generate(vettore, has_position=False, now_ms=1) is None


class TestCaricamentoDallaConfigurazione:
    """Il modello si collega da configurazione, e il file deve esserci
    davvero: un percorso sbagliato non deve diventare un bot che decide
    senza modello."""

    def test_il_generatore_carica_il_modello_dal_percorso(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        from runner import build_generator
        from signals.ml import MLSignalGenerator

        campioni = [
            Campione(
                symbol="X", ts_ms=i, valori={"a": float(i % 9) - 4}, etichetta=int(i % 9 > 4)
            )
            for i in range(200)
        ]
        percorso = tmp_path / "modello.json"
        addestra(campioni, feature=["a"], passi=200).salva(
            percorso, errore_calibrazione=0.02, campioni=200
        )

        raw = config.model_dump()
        raw["signals"]["generator"] = "ml"
        raw["signals"]["ml_enabled"] = True
        raw["signals"]["ml_model_path"] = str(percorso)
        generatore = build_generator(type(config).model_validate(raw))

        assert isinstance(generatore, MLSignalGenerator)
        assert generatore.model is not None
        assert generatore.measured_calibration_error == 0.02

    def test_un_percorso_inesistente_non_passa_in_silenzio(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        from runner import build_generator

        raw = config.model_dump()
        raw["signals"]["generator"] = "ml"
        raw["signals"]["ml_enabled"] = True
        raw["signals"]["ml_model_path"] = str(tmp_path / "non-c-e.json")
        with pytest.raises(FileNotFoundError):
            build_generator(type(config).model_validate(raw))
