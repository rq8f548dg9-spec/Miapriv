"""Test del pannello di controllo: API, guardie di sicurezza, modifica config."""

from __future__ import annotations

import asyncio
import os
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
        write_credentials(env, "k" * 32, "s" * 32)
        content = env.read_text(encoding="utf-8")
        assert f"{TESTNET_KEY_VAR}={'k' * 32}" in content
        assert "chiave-live-esistente" in content  # le chiavi live restano intatte
        assert content.count(LIVE_KEY_VAR) == 1

    @pytest.mark.skipif(
        os.name != "posix",
        reason=(
            "controlla solo il mode POSIX 600; le ACL Windows reali non sono "
            "verificate da questo test"
        ),
    )
    def test_il_file_env_non_e_leggibile_dagli_altri(self, tmp_path: Path) -> None:
        """Il mode 600 POSIX non verifica la policy ACL effettiva su Windows."""
        env = tmp_path / ".env"
        assert write_credentials(env, "k" * 32, "s" * 32) == "posix-600"
        assert oct(env.stat().st_mode)[-3:] == "600"

    def test_su_windows_restringe_le_acl_invece_del_chmod(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Su Windows ``chmod(600)`` non tocca le ACL NTFS: il file resta
        leggibile e la garanzia dichiarata sarebbe falsa."""
        import platform as modulo_platform

        import webui.server as server

        comandi: list[list[str]] = []

        def esegui(comando: list[str]) -> bool:
            comandi.append(comando)
            return True

        monkeypatch.setattr(modulo_platform, "system", lambda: "Windows")
        monkeypatch.setattr(server, "_esegui_comando", esegui)
        monkeypatch.setenv("USERNAME", "giorgio")

        env = tmp_path / ".env"
        assert write_credentials(env, "k" * 32, "s" * 32) == "windows-acl"
        assert len(comandi) == 1
        comando = comandi[0]
        assert comando[0] == "icacls"
        assert str(env) in comando
        assert "/inheritance:r" in comando
        assert any(part.startswith("giorgio:") for part in comando)

    def test_se_le_acl_non_si_applicano_non_lo_dichiara(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Meglio dire che il file non e' protetto che dichiarare una
        protezione che non c'e': l'utente puo' spostarlo o cifrarlo."""
        import platform as modulo_platform

        import webui.server as server

        monkeypatch.setattr(modulo_platform, "system", lambda: "Windows")
        monkeypatch.setattr(server, "_esegui_comando", lambda comando: False)
        monkeypatch.setenv("USERNAME", "giorgio")

        env = tmp_path / ".env"
        assert write_credentials(env, "k" * 32, "s" * 32) == "nessuna"
        # le chiavi sono comunque state scritte: servono per operare
        assert "k" * 32 in env.read_text(encoding="utf-8")

    def test_chiavi_vuote_rifiutate(self, tmp_path: Path) -> None:
        with pytest.raises(UiError, match="obbligatori"):
            write_credentials(tmp_path / ".env", "", "s")

    def test_chiavi_con_a_capo_rifiutate(self, tmp_path: Path) -> None:
        """Un a capo spezzerebbe il file .env in righe incoerenti."""
        with pytest.raises(UiError, match="non valida"):
            write_credentials(tmp_path / ".env", "k" * 20 + "\nALTRO=x", "s" * 20)


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
        assert payload["modalita"] == "paper"
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
    async def test_avvio_senza_chiavi_rifiutato_dove_servono(
        self, tmp_path: Path, raw_config: dict[str, Any]
    ) -> None:
        """In testnet gli ordini partono davvero, quindi senza chiavi non
        si parte. In paper le chiavi non esistono e l'avvio e' libero: e'
        la differenza fra le due modalita'."""
        raw = dict(raw_config)
        raw["mode"] = "testnet"
        percorso = tmp_path / "testnet.yaml"
        percorso.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        app = create_app(percorso, env_path=tmp_path / ".env")
        async with TestClient(TestServer(app)) as client:
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


class TestMessaggiDiErrore:
    """Un errore che l'utente non capisce e' un errore che non puo' correggere."""

    @pytest.mark.asyncio
    async def test_errore_di_validazione_leggibile(
        self, client: TestClient[Any, Any]
    ) -> None:
        """Il pannello non deve riversare in pagina il dump di pydantic."""
        response = await client.post("/api/config", json={"posizioni_massime": 16})
        assert response.status == 400
        message = (await response.json())["errore"]

        for rumore in ("validation error", "input_value", "type=value_error",
                       "pydantic.dev", "AppConfig", "{'mode'"):
            assert rumore not in message, f"il messaggio contiene gergo interno: {rumore}"
        assert "16" in message
        assert "Posizioni aperte al massimo" in message

    @pytest.mark.asyncio
    async def test_errore_su_un_limite_numerico_leggibile(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post("/api/config", json={"probabilita_minima": 1.5})
        assert response.status == 400
        message = (await response.json())["errore"]
        assert "pydantic" not in message.lower()
        assert "Probabilità minima" in message

    @pytest.mark.asyncio
    async def test_errore_di_coerenza_leggibile(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post("/api/config", json={"esposizione_massima": 100000})
        assert response.status == 400
        message = (await response.json())["errore"]
        assert "Esposizione massima totale" in message
        assert "input_value" not in message


class TestFinestraDelBacktest:
    """Lo storico scaricato dal pannello sono "gli ultimi N giorni", mentre la
    finestra nel file e' una data fissa: le due cose possono non sovrapporsi."""

    @pytest.fixture
    def config_con_dati_recenti(
        self, tmp_path: Path, raw_config: dict[str, Any]
    ) -> Path:
        """Candele di ieri, finestra di backtest ferma a giugno: il caso reale."""
        import time
        from decimal import Decimal

        from core.types import Candle
        from data.store import ParquetStore

        store_path = tmp_path / "store"
        store = ParquetStore(store_path)
        start = int(time.time() * 1000) - 2 * 86_400_000
        price = Decimal("60000")
        candles = []
        for i in range(1_500):
            price = price + Decimal((i % 7) - 3)
            candles.append(
                Candle(
                    symbol="BTCUSDT",
                    open_time_ms=start + i * 60_000,
                    close_time_ms=start + (i + 1) * 60_000 - 1,
                    open=price,
                    high=price + Decimal(5),
                    low=price - Decimal(5),
                    close=price,
                    volume=Decimal(10),
                    quote_volume=price * 10,
                    trades=20,
                    is_closed=True,
                )
            )
        store.add_candles(candles)
        store.flush()

        raw_config["data"]["parquet_root"] = str(store_path)
        raw_config["symbols"] = [
            s for s in raw_config["symbols"] if s["symbol"] == "BTCUSDT"
        ]
        raw_config["risk"]["limits"]["max_open_positions"] = 1
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw_config, sort_keys=False), encoding="utf-8")
        return path

    @pytest.mark.asyncio
    async def test_usa_i_dati_disponibili_se_la_finestra_non_li_copre(
        self, config_con_dati_recenti: Path, tmp_path: Path
    ) -> None:
        """Non deve dire «scarica lo storico» quando lo storico c'e' gia'."""
        import asyncio

        app = create_app(config_con_dati_recenti, env_path=tmp_path / ".env")
        async with TestClient(TestServer(app)) as client:
            assert (await client.post("/api/backtest")).status == 200
            payload: dict[str, Any] = {"lavoro": {"stato": "in corso"}}
            for _ in range(200):
                await asyncio.sleep(0.05)
                payload = await (await client.get("/api/lavoro")).json()
                if payload["lavoro"]["stato"] != "in corso":
                    break

        lavoro = payload["lavoro"]
        assert lavoro["stato"] == "completato", lavoro["messaggio"]
        assert lavoro["risultato"]["barre"] == 1_500
        assert lavoro["risultato"]["periodo"]["nota"] == "tutto lo storico disponibile"

    @pytest.mark.asyncio
    async def test_senza_nessuna_candela_lo_dice_chiaramente(
        self, config_file: Path, tmp_path: Path
    ) -> None:
        import asyncio

        raw = yaml.safe_load(
            await asyncio.to_thread(config_file.read_text, encoding="utf-8")
        )
        raw["data"]["parquet_root"] = str(tmp_path / "vuoto")
        await asyncio.to_thread(
            config_file.write_text,
            yaml.safe_dump(raw, sort_keys=False),
            encoding="utf-8",
        )

        app = create_app(config_file, env_path=tmp_path / ".env")
        async with TestClient(TestServer(app)) as client:
            await client.post("/api/backtest")
            payload: dict[str, Any] = {"lavoro": {"stato": "in corso"}}
            for _ in range(100):
                await asyncio.sleep(0.05)
                payload = await (await client.get("/api/lavoro")).json()
                if payload["lavoro"]["stato"] != "in corso":
                    break
        assert payload["lavoro"]["stato"] == "fallito"
        assert "passo 2" in payload["lavoro"]["messaggio"]


class TestRichiesteDaAltriSiti:
    """Il pannello non chiede password: chiunque abbia accesso al computer lo
    usa. Questo pero' vale anche per una pagina web aperta nel browser, che
    puo' inviare richieste a 127.0.0.1 a insaputa di chi naviga."""

    @pytest.mark.asyncio
    async def test_una_pagina_esterna_non_puo_avviare_il_bot(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post(
            "/api/avvia", headers={"Origin": "https://sito-malevolo.example"}
        )
        assert response.status == 403
        assert "altro sito" in (await response.json())["errore"]

    @pytest.mark.asyncio
    async def test_una_pagina_esterna_non_puo_chiudere_le_posizioni(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post(
            "/api/chiudi-tutto", headers={"Origin": "https://sito-malevolo.example"}
        )
        assert response.status == 403

    @pytest.mark.asyncio
    async def test_blocca_anche_la_scrittura_delle_chiavi(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post(
            "/api/credenziali",
            json={"api_key": "k", "api_secret": "s"},
            headers={"Origin": "https://sito-malevolo.example"},
        )
        assert response.status == 403

    @pytest.mark.asyncio
    async def test_blocca_un_invio_da_modulo_html(
        self, client: TestClient[Any, Any]
    ) -> None:
        """Un form su un sito esterno non richiede preflight: e' la via
        d'attacco piu' concreta, e va chiusa dall'intestazione del browser."""
        response = await client.post(
            "/api/ferma",
            data="qualunque=cosa",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert response.status == 403

    @pytest.mark.asyncio
    async def test_il_pannello_stesso_funziona(
        self, client: TestClient[Any, Any]
    ) -> None:
        """Le richieste della pagina portano Sec-Fetch-Site: same-origin."""
        response = await client.post(
            "/api/ferma", headers={"Sec-Fetch-Site": "same-origin"}
        )
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_le_letture_restano_libere(
        self, client: TestClient[Any, Any]
    ) -> None:
        """Una GET da un altro sito non puo' leggere la risposta (niente CORS),
        quindi non serve bloccarla e rompere i segnalibri."""
        response = await client.get(
            "/api/stato", headers={"Origin": "https://sito-esterno.example"}
        )
        assert response.status == 200


class TestScritturaDelleChiavi:
    """Il file .env contiene la variabile che autorizza il denaro reale: chi
    riesce a scriverci una riga arbitraria disarma la guardia del progetto."""

    @pytest.mark.parametrize(
        "separatore",
        # I caratteri su cui str.splitlines() spezza oltre a \n e \r.
        ["\x85", "\u2028", "\u2029", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e"],
    )
    def test_nessun_separatore_di_riga_passa(
        self, tmp_path: Path, separatore: str
    ) -> None:
        """str.splitlines() spezza su molti caratteri oltre a \\n e \\r: un
        controllo che ne blocca due su dieci non e' un controllo."""
        veleno = f"AAA{separatore}I_UNDERSTAND_LIVE_TRADING_RISK=yes"
        with pytest.raises(UiError):
            write_credentials(tmp_path / ".env", veleno, "BBB")

    def test_la_guardia_sul_denaro_reale_non_finisce_nel_file(
        self, tmp_path: Path
    ) -> None:
        env = tmp_path / ".env"
        with pytest.raises(UiError):
            write_credentials(
                env, "AAA\x85I_UNDERSTAND_LIVE_TRADING_RISK=yes", "BBB"
            )
        testo = env.read_text(encoding="utf-8") if env.exists() else ""
        assert "I_UNDERSTAND_LIVE_TRADING_RISK" not in testo

    def test_una_chiave_normale_viene_accettata(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        write_credentials(env, "a" * 64, "b" * 64)
        assert f"{TESTNET_KEY_VAR}={'a' * 64}" in env.read_text(encoding="utf-8")

    def test_una_chiave_troppo_corta_viene_rifiutata(self, tmp_path: Path) -> None:
        with pytest.raises(UiError):
            write_credentials(tmp_path / ".env", "corta", "b" * 64)

    def test_caratteri_strani_rifiutati(self, tmp_path: Path) -> None:
        with pytest.raises(UiError):
            write_credentials(tmp_path / ".env", "chiave con spazi " + "a" * 50, "b" * 64)


class TestIndirizzoDelPannello:
    """Una pagina esterna puo' far risolvere il proprio nome a 127.0.0.1
    (DNS rebinding): a quel punto il browser la considera stessa origine e i
    controlli su Origin non bastano piu'. Resta il nome usato per chiamarci."""

    @pytest.mark.asyncio
    async def test_un_host_estraneo_viene_rifiutato(
        self, client: TestClient[Any, Any]
    ) -> None:
        response = await client.post(
            "/api/ferma",
            headers={"Host": "sito-malevolo.example:8777", "Sec-Fetch-Site": "same-origin"},
        )
        assert response.status == 403

    @pytest.mark.asyncio
    async def test_localhost_e_ammesso(self, client: TestClient[Any, Any]) -> None:
        port = client.server.port
        response = await client.post(
            "/api/ferma", headers={"Host": f"localhost:{port}"}
        )
        assert response.status == 200


class TestAttivita:
    """«L'ho fatto partire ma non succede niente» e' la risposta giusta a un
    pannello che non dice cosa sta facendo. Il bot in realta' sta
    raccogliendo candele: finche' non ne ha abbastanza non puo' decidere, e
    questo deve vedersi, insieme al motivo per cui non opera."""

    def _supervisore(self, config_file: Path) -> BotSupervisor:
        from config.schema import load_config

        return BotSupervisor(config_path=config_file, config=load_config(config_file))

    def test_a_bot_fermo_lo_dice(self, config_file: Path) -> None:
        attivita = self._supervisore(config_file).status()["attivita"]
        assert attivita["stato"] == "fermo"
        assert attivita["flusso_attivo"] is False

    def test_durante_il_preriscaldamento_mostra_le_barre_che_mancano(
        self, config_file: Path, tmp_path: Path
    ) -> None:
        from config.schema import load_config
        from core.types import Candle
        from tests.test_runner import D, make_bot

        config = load_config(config_file)
        supervisor = self._supervisore(config_file)
        bot = make_bot(config, tmp_path)
        for i in range(3):
            bot.features.on_candle(
                Candle(
                    symbol="BTCUSDT",
                    open_time_ms=1_700_000_000_000 + i * 60_000,
                    close_time_ms=1_700_000_000_000 + (i + 1) * 60_000 - 1,
                    open=D("1"), high=D("1"), low=D("1"), close=D("1"),
                    volume=D("1"), quote_volume=D("1"), trades=1, is_closed=True,
                )
            )
        bot.running = True
        supervisor.bot = bot

        attivita = supervisor.status()["attivita"]
        per_simbolo = {s["simbolo"]: s for s in attivita["simboli"]}
        assert per_simbolo["BTCUSDT"]["barre"] == 3
        assert per_simbolo["BTCUSDT"]["barre_necessarie"] == config.features.warmup_bars
        assert per_simbolo["BTCUSDT"]["pronto"] is False
        assert "dati" in attivita["stato"] or "candele" in attivita["stato"]
        assert attivita["spiegazione"]

    def test_a_dati_pronti_dichiara_che_sta_cercando_un_segnale(
        self, config_file: Path, tmp_path: Path
    ) -> None:
        from config.schema import load_config
        from core.types import Candle
        from tests.test_runner import D, make_bot

        config = load_config(config_file)
        supervisor = self._supervisore(config_file)
        bot = make_bot(config, tmp_path)
        for symbol, partenza in (("BTCUSDT", D("60000")), ("ETHUSDT", D("3000"))):
            prezzo = partenza
            for i in range(config.features.warmup_bars + 1):
                prezzo = (prezzo * D("1.0002")).quantize(D("0.01"))
                bot.features.on_candle(
                    Candle(
                        symbol=symbol,
                        open_time_ms=1_700_000_000_000 + i * 60_000,
                        close_time_ms=1_700_000_000_000 + (i + 1) * 60_000 - 1,
                        open=prezzo, high=prezzo * D("1.001"), low=prezzo * D("0.999"),
                        close=prezzo, volume=D("10"), quote_volume=prezzo * D("10"),
                        trades=10, is_closed=True,
                    )
                )
        bot.running = True
        supervisor.bot = bot

        attivita = supervisor.status()["attivita"]
        per_simbolo = {s["simbolo"]: s for s in attivita["simboli"]}
        assert per_simbolo["BTCUSDT"]["pronto"] is True
        assert "segnale" in attivita["stato"]

    def test_mostra_leta_dellultimo_dato_ricevuto(
        self, config_file: Path, tmp_path: Path
    ) -> None:
        """Un flusso caduto si riconosce da qui: il bot gira, i dati no."""
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(config_file)
        supervisor = self._supervisore(config_file)
        bot = make_bot(config, tmp_path)
        bot.last_data_ms = bot.clock.now_ms() - 45_000
        bot.running = True
        supervisor.bot = bot

        attivita = supervisor.status()["attivita"]
        assert attivita["eta_dato_s"] == pytest.approx(45.0, abs=1.0)


class TestCapitale:
    """Il pannello deve dire tre numeri separati: quanto hai messo, quanto
    hai guadagnato, quanto c'e' adesso. Se li somma in uno solo, un
    versamento sembra un profitto."""

    @pytest.fixture
    def config_isolato(self, tmp_path: Path, raw_config: dict[str, Any]) -> Path:
        raw = dict(raw_config)
        raw["monitoring"] = dict(raw["monitoring"])
        raw["monitoring"]["state_path"] = str(tmp_path / "stato" / "bot_state.json")
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return path

    @pytest.fixture
    async def client_isolato(self, config_isolato: Path, tmp_path: Path) -> Any:
        app = create_app(config_isolato, env_path=tmp_path / ".env")
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    async def test_al_primo_avvio_mostra_il_capitale_dichiarato(
        self, client_isolato: TestClient[Any, Any]
    ) -> None:
        payload = await (await client_isolato.get("/api/capitale")).json()
        assert payload["conferito"] == "200"
        assert payload["prelevato"] == "0"
        assert payload["guadagno_netto"] == "0"
        assert payload["in_gestione"] == "200"

    async def test_un_conferimento_alza_la_base_senza_essere_un_guadagno(
        self, client_isolato: TestClient[Any, Any]
    ) -> None:
        risposta = await client_isolato.post(
            "/api/capitale/movimento", json={"tipo": "conferimento", "importo": "50"}
        )
        assert risposta.status == 200
        payload = await (await client_isolato.get("/api/capitale")).json()
        assert payload["conferito"] == "250"
        assert payload["guadagno_netto"] == "0"
        assert payload["in_gestione"] == "250"

    async def test_un_prelievo_oltre_il_disponibile_viene_rifiutato(
        self, client_isolato: TestClient[Any, Any]
    ) -> None:
        risposta = await client_isolato.post(
            "/api/capitale/movimento", json={"tipo": "prelievo", "importo": "10000"}
        )
        assert risposta.status == 400
        messaggio = (await risposta.json())["errore"]
        assert "liquid" in messaggio
        payload = await (await client_isolato.get("/api/capitale")).json()
        assert payload["in_gestione"] == "200"

    async def test_un_tipo_inventato_viene_rifiutato(
        self, client_isolato: TestClient[Any, Any]
    ) -> None:
        risposta = await client_isolato.post(
            "/api/capitale/movimento", json={"tipo": "regalo", "importo": "10"}
        )
        assert risposta.status == 400

    async def test_il_pannello_non_sposta_denaro_vero(
        self, client_isolato: TestClient[Any, Any]
    ) -> None:
        """Registrare un versamento e' contabilita', non un bonifico: il
        trasferimento lo fa la persona sull'exchange. Il bot non ha e non
        deve avere il permesso di prelievo."""
        payload = await (await client_isolato.get("/api/capitale")).json()
        assert payload["avvertenza"]
        assert "exchange" in payload["avvertenza"].lower()


class TestErroriDelPannelloNonDiventano500:
    """Un errore che l'utente puo' correggere deve arrivargli scritto. Se
    diventa un 500, il pannello mostra solo "errore 500" e la persona non
    ha idea di cosa sia andato storto: e' successo davvero con il modulo
    dei movimenti di capitale, che inviava il corpo senza dichiararlo
    JSON."""

    async def test_corpo_senza_content_type_e_un_errore_leggibile(
        self, client: TestClient[Any, Any]
    ) -> None:
        risposta = await client.post(
            "/api/capitale/movimento",
            data='{"tipo": "conferimento", "importo": "1"}',
        )
        assert risposta.status == 400
        assert "JSON" in (await risposta.json())["errore"]

    async def test_vale_per_tutti_gli_endpoint_con_un_corpo(
        self, client: TestClient[Any, Any]
    ) -> None:
        risposta = await client.post("/api/scarica-dati", data='{"giorni": 1}')
        assert risposta.status == 400
        assert (await risposta.json())["errore"]


class TestEsploratoreFrequenza:
    """La risposta a «voglio che operi in continuazione» non e' si' o no:
    e' una tabella. Il pannello deve mostrare quante operazioni produce
    ogni impostazione e quanto costano, misurate sui dati dell'utente."""

    @pytest.fixture
    def config_con_dati(self, tmp_path: Path, raw_config: dict[str, Any]) -> Path:
        from core.types import Candle
        from data.store import ParquetStore
        from tests.test_runner import D

        store_path = tmp_path / "store"
        raw = dict(raw_config)
        raw["data"] = dict(raw["data"])
        raw["data"]["parquet_root"] = str(store_path)
        raw["monitoring"] = dict(raw["monitoring"])
        raw["monitoring"]["state_path"] = str(tmp_path / "stato" / "bot_state.json")
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        store = ParquetStore(store_path)
        prezzo = D("60000")
        candele = []
        for i in range(600):
            prezzo = (prezzo * (D("1.0004") if i % 3 else D("0.9996"))).quantize(D("0.01"))
            candele.append(
                Candle(
                    symbol="BTCUSDT",
                    open_time_ms=1_700_000_000_000 + i * 60_000,
                    close_time_ms=1_700_000_000_000 + (i + 1) * 60_000 - 1,
                    open=prezzo, high=prezzo * D("1.001"), low=prezzo * D("0.999"),
                    close=prezzo, volume=D("10"), quote_volume=prezzo * D("10"),
                    trades=20, is_closed=True,
                )
            )
        store.add_candles(candele)
        store.flush()
        return path

    @pytest.fixture
    async def client_con_dati(self, config_con_dati: Path, tmp_path: Path) -> Any:
        app = create_app(config_con_dati, env_path=tmp_path / ".env")
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    async def test_restituisce_una_riga_per_impostazione(
        self, client_con_dati: TestClient[Any, Any]
    ) -> None:
        avvio = await client_con_dati.post("/api/frequenza", json={})
        assert avvio.status == 200

        for _ in range(100):
            stato = await (await client_con_dati.get("/api/stato")).json()
            lavoro = stato.get("lavoro")
            if lavoro and lavoro["stato"] != "in corso":
                break
            await asyncio.sleep(0.2)

        assert lavoro["stato"] == "completato", lavoro.get("messaggio")
        righe = lavoro["risultato"]["profili"]
        assert len(righe) >= 3
        for riga in righe:
            assert "operazioni_al_giorno" in riga
            assert "costo_giro_bps" in riga
            assert riga["esito"] in {"positivo", "negativo", "insufficiente"}

    async def test_senza_dati_lo_dice(self, client: TestClient[Any, Any]) -> None:
        risposta = await client.post("/api/frequenza", json={})
        assert risposta.status == 400
        assert "storico" in (await risposta.json())["errore"]


class TestScenariNelPannello:
    """Un backtest racconta la storia capitata. Il pannello deve dire
    anche quante storie diverse erano possibili: con un campione piccolo
    lo dichiara invece di inventare percentili."""

    async def test_la_prova_riporta_gli_scenari(
        self, client: TestClient[Any, Any], tmp_path: Path
    ) -> None:
        from backtest.scenari import simula_scenari
        from core.portfolio import TradeResult
        from tests.test_runner import D

        operazioni = [
            TradeResult(symbol="BTCUSDT", ts_ms=i, realized_pnl=D("1" if i % 3 else "-1"))
            for i in range(60)
        ]
        risultato = simula_scenari(operazioni, capitale=D("200"), percorsi=200, seed=1)
        assert risultato.affidabile
        assert "percentili" in risultato.to_dict()


class TestEsperimentiDalPannello:
    async def test_senza_dati_lo_dice(self, client: TestClient[Any, Any]) -> None:
        risposta = await client.post("/api/esperimenti", json={})
        assert risposta.status == 400
        assert "storico" in (await risposta.json())["errore"]

    def test_i_candidati_standard_non_toccano_le_protezioni(self) -> None:
        """Se un giorno qualcuno ne aggiungesse uno sul kill switch, il
        costruttore lo rifiuterebbe: questo test lo rende visibile."""
        from apprendimento.esperimenti import CANDIDATI_STANDARD

        for candidato in CANDIDATI_STANDARD:
            for percorso in candidato.modifiche:
                assert not percorso.startswith(("risk.", "mode", "exchange."))


class TestChiaviInPaper:
    """Salvare le chiavi in modalita' paper dava «la modalita' backtest
    non usa chiavi API»: un messaggio che parla di una modalita' diversa
    da quella attiva, su un'operazione che e' perfettamente sensata.
    Le chiavi si preparano prima, si usano dopo."""

    async def test_la_verifica_prova_la_testnet_anche_da_paper(
        self, client: TestClient[Any, Any], tmp_path: Path
    ) -> None:
        risposta = await client.post(
            "/api/test-connessione", json={}
        )
        messaggio = (await risposta.json()).get("errore", "")
        assert "backtest" not in messaggio, (
            "in paper il messaggio non deve parlare di backtest"
        )

    async def test_il_messaggio_dice_cosa_fare(
        self, client: TestClient[Any, Any]
    ) -> None:
        risposta = await client.post("/api/test-connessione", json={})
        if risposta.status == 400:
            messaggio = (await risposta.json())["errore"]
            # Senza chiavi salvate deve dire che mancano, non che la
            # modalita' non le usa.
            assert "chiav" in messaggio.lower()
