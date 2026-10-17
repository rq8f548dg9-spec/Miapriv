"""Test della configurazione: validazione fail-fast e guardia denaro reale."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from config.credentials import (
    LIVE_KEY_VAR,
    LIVE_SECRET_VAR,
    TESTNET_KEY_VAR,
    TESTNET_SECRET_VAR,
    CredentialsError,
    load_credentials,
    load_dotenv,
)
from config.live_guard import (
    LIVE_TRADING_ENV_VALUE,
    LIVE_TRADING_ENV_VAR,
    LiveTradingNotAuthorizedError,
)
from config.schema import ConfigError, Mode, load_config
from tests.conftest import WriteConfig


class TestDefaultConfig:
    def test_config_di_default_e_valida(self) -> None:
        config = load_config(Path("config/config.yaml"))
        assert config.mode is Mode.TESTNET

    def test_il_default_del_repository_e_testnet(self) -> None:
        """Il file versionato non deve mai arrivare su live per distrazione."""
        config = load_config(Path("config/config.yaml"))
        assert not config.mode.uses_real_money

    def test_url_coerente_con_la_modalita(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        testnet = load_config(write_config(base_config_dict))
        assert "testnet" in testnet.rest_url
        assert "testnet" in testnet.ws_url

    @pytest.mark.parametrize(
        "campo,valore",
        [
            ("testnet_rest_url", "https://api.binance.com"),
            ("testnet_rest_url", "https://api3.binance.com"),
            ("testnet_ws_url", "wss://stream.binance.com:9443/stream"),
        ],
    )
    def test_la_prova_non_puo_puntare_al_mercato_vero(
        self,
        base_config_dict: dict[str, Any],
        write_config: WriteConfig,
        campo: str,
        valore: str,
    ) -> None:
        """Con mode=testnet e l'endpoint di produzione, il bot opererebbe con
        denaro vero credendo di provare. La guardia sul denaro reale non
        guarda l'indirizzo: deve guardarlo la configurazione."""
        base_config_dict["exchange"][campo] = valore
        with pytest.raises(ValueError, match=r"produzione|testnet"):
            load_config(write_config(base_config_dict))

    def test_un_endpoint_finto_locale_resta_ammesso(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        """Puntare a un server finto sul proprio computer e' il modo normale
        di provare il bot senza toccare Binance."""
        base_config_dict["exchange"]["testnet_rest_url"] = "http://127.0.0.1:8080"
        base_config_dict["exchange"]["testnet_ws_url"] = "ws://127.0.0.1:8080/stream"
        config = load_config(write_config(base_config_dict))
        assert config.rest_url == "http://127.0.0.1:8080"

    def test_gli_url_live_non_possono_puntare_alla_testnet(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        """Il contrario e' meno grave ma e' comunque una bugia: un conto
        dichiarato reale che opera sulla prova non e' quello che si crede."""
        base_config_dict["exchange"]["live_rest_url"] = "https://testnet.binance.vision"
        with pytest.raises(ValueError, match=r"live|testnet"):
            load_config(write_config(base_config_dict))


class TestLiveGuard:
    """La guardia e' l'unico punto che autorizza il denaro reale."""

    def test_live_senza_variabile_ambiente_non_parte(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["mode"] = "live"
        with pytest.raises(LiveTradingNotAuthorizedError):
            load_config(write_config(base_config_dict))

    def test_live_con_autorizzazione_parte(
        self,
        base_config_dict: dict[str, Any],
        write_config: WriteConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(LIVE_TRADING_ENV_VAR, LIVE_TRADING_ENV_VALUE)
        base_config_dict["mode"] = "live"
        config = load_config(write_config(base_config_dict))
        assert config.mode is Mode.LIVE
        assert config.rest_url == config.exchange.live_rest_url

    @pytest.mark.parametrize("value", ["YES", "Yes", "true", "1", "y", "", "si"])
    def test_valori_simili_a_yes_non_autorizzano(
        self,
        base_config_dict: dict[str, Any],
        write_config: WriteConfig,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
    ) -> None:
        """Nessuna normalizzazione tollerante: solo il valore esatto autorizza."""
        monkeypatch.setenv(LIVE_TRADING_ENV_VAR, value)
        base_config_dict["mode"] = "live"
        with pytest.raises(LiveTradingNotAuthorizedError):
            load_config(write_config(base_config_dict))

    def test_la_guardia_logga_capitale_e_limiti(
        self,
        base_config_dict: dict[str, Any],
        write_config: WriteConfig,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv(LIVE_TRADING_ENV_VAR, LIVE_TRADING_ENV_VALUE)
        base_config_dict["mode"] = "live"
        with caplog.at_level("WARNING"):
            load_config(write_config(base_config_dict))
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "LIVE TRADING ATTIVO" in messages
        assert "200" in messages
        assert "max_daily_drawdown_pct" in messages

    def test_testnet_e_backtest_non_chiamano_la_guardia(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        for mode in ("testnet", "backtest"):
            base_config_dict["mode"] = mode
            config = load_config(write_config(base_config_dict, f"{mode}.yaml"))
            assert not config.mode.uses_real_money


class TestFailFast:
    """Una configurazione incoerente deve fermare l'avvio, non degradare."""

    def test_chiave_sconosciuta_rifiutata(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["risk"]["sizing"]["kely_fraction"] = 0.25  # refuso volontario
        with pytest.raises(ConfigError, match="non valida"):
            load_config(write_config(base_config_dict))

    def test_kelly_oltre_un_quarto_rifiutato(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["risk"]["sizing"]["kelly_fraction"] = 0.5
        with pytest.raises(ConfigError):
            load_config(write_config(base_config_dict))

    def test_esposizione_oltre_il_capitale_rifiutata(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        """Spot senza leva: esporre piu' del capitale e' impossibile."""
        base_config_dict["risk"]["limits"]["max_gross_exposure_quote"] = 500
        with pytest.raises(ConfigError, match="leva"):
            load_config(write_config(base_config_dict))

    def test_ordine_minimo_sopra_il_massimo_rifiutato(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["risk"]["sizing"]["min_order_quote"] = 50
        base_config_dict["risk"]["sizing"]["max_order_quote"] = 20
        with pytest.raises(ConfigError):
            load_config(write_config(base_config_dict))

    def test_drawdown_giornaliero_oltre_il_totale_rifiutato(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["risk"]["killswitch"]["max_daily_drawdown_pct"] = 0.20
        base_config_dict["risk"]["killswitch"]["max_total_drawdown_pct"] = 0.10
        with pytest.raises(ConfigError):
            load_config(write_config(base_config_dict))

    def test_posizioni_massime_oltre_i_simboli_abilitati_rifiutate(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["risk"]["limits"]["max_open_positions"] = 10
        with pytest.raises(ConfigError, match="limite inefficace"):
            load_config(write_config(base_config_dict))

    def test_quote_mista_rifiutata(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["symbols"][0]["symbol"] = "BTCEUR"
        base_config_dict["symbols"][0]["quote_asset"] = "EUR"
        with pytest.raises(ConfigError, match="quote diversa"):
            load_config(write_config(base_config_dict))

    def test_nessun_simbolo_abilitato_rifiutato(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        for symbol in base_config_dict["symbols"]:
            symbol["enabled"] = False
        with pytest.raises(ConfigError, match="nessun simbolo abilitato"):
            load_config(write_config(base_config_dict))

    def test_backtest_senza_sezione_backtest_rifiutato(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["mode"] = "backtest"
        base_config_dict.pop("backtest")
        with pytest.raises(ConfigError, match="richiede la sezione"):
            load_config(write_config(base_config_dict))

    def test_finestra_di_backtest_vuota_rifiutata(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["backtest"]["end"] = base_config_dict["backtest"]["start"]
        with pytest.raises(ConfigError):
            load_config(write_config(base_config_dict))

    def test_ml_abilitato_senza_modello_rifiutato(
        self, base_config_dict: dict[str, Any], write_config: WriteConfig
    ) -> None:
        base_config_dict["signals"]["ml_enabled"] = True
        with pytest.raises(ConfigError, match="ml_model_path"):
            load_config(write_config(base_config_dict))

    def test_file_mancante(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="non trovato"):
            load_config(tmp_path / "inesistente.yaml")

    def test_configurazione_immutabile(self) -> None:
        """Nessun componente puo' allentare un limite a runtime."""
        config = load_config(Path("config/config.yaml"))
        with pytest.raises(ValidationError):
            config.risk.limits.max_open_positions = 99  # type: ignore[misc]


class TestCredentials:
    def test_backtest_non_richiede_chiavi(self) -> None:
        assert load_credentials(Mode.BACKTEST) is None

    def test_testnet_richiede_le_sue_chiavi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TESTNET_KEY_VAR, "k")
        monkeypatch.setenv(TESTNET_SECRET_VAR, "s")
        creds = load_credentials(Mode.TESTNET)
        assert creds is not None
        assert creds.api_key.get_secret_value() == "k"

    def test_chiavi_testnet_non_valgono_per_il_live(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nomi distinti: le chiavi testnet non possono finire su denaro reale."""
        monkeypatch.setenv(LIVE_TRADING_ENV_VAR, LIVE_TRADING_ENV_VALUE)
        monkeypatch.setenv(TESTNET_KEY_VAR, "k")
        monkeypatch.setenv(TESTNET_SECRET_VAR, "s")
        with pytest.raises(CredentialsError, match=LIVE_KEY_VAR):
            load_credentials(Mode.LIVE)

    def test_chiavi_live_non_leggibili_senza_autorizzazione(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(LIVE_KEY_VAR, "k")
        monkeypatch.setenv(LIVE_SECRET_VAR, "s")
        with pytest.raises(CredentialsError, match="autorizzazione"):
            load_credentials(Mode.LIVE)

    def test_i_segreti_non_compaiono_nelle_stringhe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(TESTNET_KEY_VAR, "chiave-segretissima")
        monkeypatch.setenv(TESTNET_SECRET_VAR, "segreto-segretissimo")
        creds = load_credentials(Mode.TESTNET)
        assert creds is not None
        for rendered in (str(creds), repr(creds), str(creds.model_dump())):
            assert "segretissim" not in rendered

    def test_dotenv_non_sovrascrive_lambiente(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"# commento\n{TESTNET_KEY_VAR}=dal-file\n{TESTNET_SECRET_VAR}='quotato'\n",
            encoding="utf-8",
        )
        monkeypatch.setenv(TESTNET_KEY_VAR, "dallambiente")
        load_dotenv(env_file)
        creds = load_credentials(Mode.TESTNET)
        assert creds is not None
        assert creds.api_key.get_secret_value() == "dallambiente"
        assert creds.api_secret.get_secret_value() == "quotato"

    def test_dotenv_assente_non_e_un_errore(self, tmp_path: Path) -> None:
        assert load_dotenv(tmp_path / "manca.env") == 0
