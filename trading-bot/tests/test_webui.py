"""Test del pannello di controllo: API, guardie di sicurezza, modifica config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from config.credentials import LIVE_KEY_VAR, TESTNET_KEY_VAR
from webui.server import (
    BotSupervisor,
    UiError,
    apply_config_changes,
    create_app,
    run_ui,
    write_credentials,
)

CONFIG_PATH = Path("config/config.yaml")


@pytest.fixture
def raw_config() -> dict[str, Any]:
    return dict(yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def config_file(tmp_path: Path, raw_config: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
async def client(config_file: Path, tmp_path: Path) -> Any:
    app = create_app(config_file, env_path=tmp_path / ".env")
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


class TestSicurezza:
    def test_il_pannello_non_si_espone_sulla_rete(self, config_file: Path) -> None:
        """Un'interfaccia che puo' muovere denaro resta su 127.0.0.1."""
        with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
            run_ui(config_file, host="0.0.0.0", open_browser=False)  # noqa: S104

    def test_la_modalita_non_e_modificabile(self, raw_config: dict[str, Any]) -> None:
        """Il passaggio a denaro reale non puo' passare da un bottone."""
        with pytest.raises(UiError, match="non modificabile"):
            apply_config_changes(raw_config, {"mode": "live"})

    def test_nessun_campo_fuori_elenco(self, raw_config: dict[str, Any]) -> None:
        with pytest.raises(UiError, match="non modificabile"):
            apply_config_changes(raw_config, {"exchange": {"live_rest_url": "http://x"}})

    @pytest.mark.asyncio
    async def test_le_chiavi_non_tornano_mai_indietro(
        self, client: TestClient[Any, Any], tmp_path: Path
    ) -> None:
        await client.post(
            "/api/credenziali", json={"api_key": "chiave-vera", "api_secret": "segreto-vero"}
        )
        for path in ("/api/stato", "/api/config"):
            body = await (await client.get(path)).text()
            assert "chiave-vera" not in body
            assert "segreto-vero" not in body

    def test_scrive_solo_le_chiavi_testnet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = tmp_path / ".env"
        env.write_text(f"{LIVE_KEY_VAR}=chiave-live-esistente\n", encoding="utf-8")
        write_credentials(env, "k", "s")
        content = env.read_text(encoding="utf-8")
        assert f"{TESTNET_KEY_VAR}=k" in content
        assert "chiave-live-esistente" in content  # le chiavi live restano intatte
        assert content.count(LIVE_KEY_VAR) == 1

    def test_il_file_env_non_e_leggibile_dagli_altri(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        write_credentials(env, "k", "s")
        assert oct(env.stat().st_mode)[-3:] == "600"

    def test_chiavi_vuote_rifiutate(self, tmp_path: Path) -> None:
        with pytest.raises(UiError, match="obbligatori"):
            write_credentials(tmp_path / ".env", "", "s")

    def test_chiavi_con_a_capo_rifiutate(self, tmp_path: Path) -> None:
        """Un a capo spezzerebbe il file .env in righe incoerenti."""
        with pytest.raises(UiError, match="a capo"):
            write_credentials(tmp_path / ".env", "k\nALTRO=x", "s")


class TestModificaConfig:
    def test_applica_un_campo_semplice(self, raw_config: dict[str, Any]) -> None:
        updated = apply_config_changes(raw_config, {"posizioni_massime": 1})
        assert updated["risk"]["limits"]["max_open_positions"] == 1

    def test_applica_un_campo_annidato(self, raw_config: dict[str, Any]) -> None:
        updated = apply_config_changes(raw_config, {"drawdown_giornaliero": 0.02})
        assert updated["risk"]["killswitch"]["max_daily_drawdown_pct"] == 0.02

    def test_non_tocca_loriginale(self, raw_config: dict[str, Any]) -> None:
        before = yaml.safe_dump(raw_config, sort_keys=True)
        apply_config_changes(raw_config, {"posizioni_massime": 1})
        assert yaml.safe_dump(raw_config, sort_keys=True) == before

    def test_abilita_e_disabilita_i_simboli(self, raw_config: dict[str, Any]) -> None:
        updated = apply_config_changes(raw_config, {"simboli_abilitati": ["BTCUSDT"]})
        enabled = {s["symbol"] for s in updated["symbols"] if s["enabled"]}
        assert enabled == {"BTCUSDT"}

    @pytest.mark.asyncio
    async def test_una_modifica_incoerente_viene_rifiutata(
        self, client: TestClient[Any, Any]
    ) -> None:
        """La validazione gira prima della scrittura: il file resta sempre valido."""
        response = await client.post("/api/config", json={"esposizione_massima": 100000})
        assert response.status == 400
        body = await response.json()
        assert "leva" in body["errore"]

    @pytest.mark.asyncio
    async def test_una_modifica_valida_viene_salvata(
        self, client: TestClient[Any, Any], config_file: Path
    ) -> None:
        import asyncio

        response = await client.post("/api/config", json={"posizioni_massime": 1})
        assert response.status == 200
        text = await asyncio.to_thread(config_file.read_text, encoding="utf-8")
        saved = yaml.safe_load(text)
        assert saved["risk"]["limits"]["max_open_positions"] == 1

    @pytest.mark.asyncio
    async def test_kelly_oltre_il_tetto_rifiutato_anche_dal_pannello(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post("/api/config", json={"probabilita_minima": 1.5})
        assert response.status == 400


class TestApi:
    @pytest.mark.asyncio
    async def test_la_pagina_si_apre(self, client: TestClient[Any, Any]) -> None:
        response = await client.get("/")
        assert response.status == 200
        assert "Trading bot" in await response.text()

    @pytest.mark.asyncio
    async def test_stato_a_bot_fermo(self, client: TestClient[Any, Any]) -> None:
        payload = await (await client.get("/api/stato")).json()
        assert payload["in_esecuzione"] is False
        assert payload["modalita"] == "testnet"
        assert payload["denaro_reale"] is False
        assert payload["killswitch"]["scattato"] is False

    @pytest.mark.asyncio
    async def test_decisioni_vuote_a_bot_fermo(self, client: TestClient[Any, Any]) -> None:
        payload = await (await client.get("/api/decisioni")).json()
        assert payload["decisioni"] == []

    @pytest.mark.asyncio
    async def test_equity_vuota_a_bot_fermo(self, client: TestClient[Any, Any]) -> None:
        payload = await (await client.get("/api/equity")).json()
        assert payload["punti"] == []

    @pytest.mark.asyncio
    async def test_config_esposta_con_i_campi_modificabili(
        self, client: TestClient[Any, Any]
    ) -> None:
        payload = await (await client.get("/api/config")).json()
        assert "capitale" in payload["modificabili"]
        assert payload["simboli"]

    @pytest.mark.asyncio
    async def test_avvio_senza_chiavi_rifiutato(self, client: TestClient[Any, Any]) -> None:
        response = await client.post("/api/avvia")
        assert response.status == 400
        assert "chiavi" in (await response.json())["errore"]

    @pytest.mark.asyncio
    async def test_chiudi_tutto_a_bot_fermo(self, client: TestClient[Any, Any]) -> None:
        response = await client.post("/api/chiudi-tutto")
        assert response.status == 400
        assert "non e' in esecuzione" in (await response.json())["errore"]

    @pytest.mark.asyncio
    async def test_reset_killswitch_a_bot_fermo(self, client: TestClient[Any, Any]) -> None:
        assert (await client.post("/api/killswitch-reset")).status == 400

    @pytest.mark.asyncio
    async def test_ferma_e_idempotente(self, client: TestClient[Any, Any]) -> None:
        assert (await client.post("/api/ferma")).status == 200
        assert (await client.post("/api/ferma")).status == 200

    @pytest.mark.asyncio
    async def test_backtest_senza_dati_lo_dice(self, client: TestClient[Any, Any]) -> None:
        """Il messaggio deve spiegare cosa fare, non limitarsi a fallire."""
        import asyncio

        assert (await client.post("/api/backtest")).status == 200
        payload: dict[str, Any] = {"lavoro": {"stato": "in corso", "messaggio": ""}}
        for _ in range(100):
            await asyncio.sleep(0.05)
            payload = await (await client.get("/api/lavoro")).json()
            if payload["lavoro"]["stato"] != "in corso":
                break
        assert payload["lavoro"]["stato"] == "fallito"
        assert "scarica" in payload["lavoro"]["messaggio"].lower()


class TestSupervisore:
    def test_conta_le_candele_salvate(self, config_file: Path, tmp_path: Path) -> None:
        from decimal import Decimal

        from core.types import Candle
        from data.store import ParquetStore

        store_path = tmp_path / "store"
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        raw["data"]["parquet_root"] = str(store_path)
        config_file.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        store = ParquetStore(store_path)
        store.add_candles(
            [
                Candle(
                    symbol="BTCUSDT",
                    open_time_ms=1_700_000_000_000 + i * 60_000,
                    close_time_ms=1_700_000_000_000 + (i + 1) * 60_000 - 1,
                    open=Decimal("1"), high=Decimal("1"), low=Decimal("1"),
                    close=Decimal("1"), volume=Decimal("1"), quote_volume=Decimal("1"),
                    trades=1, is_closed=True,
                )
                for i in range(7)
            ]
        )
        store.flush()

        from config.schema import load_config

        supervisor = BotSupervisor(config_path=config_file, config=load_config(config_file))
        assert supervisor.stored_candles()["totale"] == 7

    def test_stato_senza_bot_mostra_il_capitale_configurato(self, config_file: Path) -> None:
        from config.schema import load_config

        supervisor = BotSupervisor(config_path=config_file, config=load_config(config_file))
        status = supervisor.status()
        assert status["equity"] == "200"
        assert status["posizioni"] == []
