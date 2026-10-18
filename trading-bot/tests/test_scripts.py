"""Test degli script accessori: portabilita' e coerenza con la configurazione.

Sono gli unici pezzi che girano fuori dal processo del bot, ed e' li' che si
nascondono le rotture per sistema operativo: l'utente che apre il programma su
Windows non deve incontrare un errore che qui non si vedrebbe mai.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from decimal import Decimal
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import SecretStr

from config.credentials import Credentials
from config.schema import INTERVAL_MS, DataConfig, Mode, load_config


class TestIntervalliScaricabili:
    def test_ogni_intervallo_ammesso_ha_una_durata(self) -> None:
        """Se la configurazione accetta un intervallo, lo scaricatore deve
        saperlo scaricare: due elenchi separati divergono, e il download
        finisce con un KeyError in faccia all'utente."""
        from scripts.fetch_history import step_ms_for

        for interval in INTERVAL_MS:
            config = DataConfig(candle_interval=interval)
            assert step_ms_for(config.candle_interval) > 0

    def test_un_intervallo_sconosciuto_resta_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="candle_interval"):
            DataConfig(candle_interval="7m")


class _VietaResource:
    """Simula un sistema senza il modulo ``resource`` (cioe' Windows)."""

    def find_spec(
        self, name: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if name == "resource":
            raise ModuleNotFoundError("No module named 'resource'")
        return None


class TestBenchmarkSuWindows:
    def test_si_importa_e_gira_senza_il_modulo_resource(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Su Windows ``import resource`` non esiste: il benchmark si fermava
        prima ancora di stampare l'aiuto."""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delitem(sys.modules, "resource", raising=False)
        monkeypatch.setattr(sys, "meta_path", [_VietaResource(), *sys.meta_path])

        import scripts.benchmark as benchmark

        ricaricato = importlib.reload(benchmark)
        try:
            assert ricaricato.picco_memoria_mb() is None
        finally:
            monkeypatch.undo()
            importlib.reload(benchmark)

    def test_dove_il_sistema_lo_espone_la_misura_c_e(self) -> None:
        import scripts.benchmark as benchmark

        picco = benchmark.picco_memoria_mb()
        assert picco is None or picco > 0


class TestScaricoDelFlussoOrdini:
    """Senza il flusso ordini storico il backtest resta cieco su meta'
    degli ingressi. L'endpoint aggTrades e' pubblico come quello delle
    candele: non servono chiavi, serve solo chiederlo."""

    @pytest.mark.asyncio
    async def test_gli_aggtrades_diventano_trade(self) -> None:
        from execution.binance import BinanceBroker, HttpResponse
        from tests.test_execution import FakeHttp, make_broker

        righe = [
            {"a": 100, "p": "60000.00", "q": "0.10", "T": 1_700_000_000_000, "m": True},
            {"a": 101, "p": "60001.00", "q": "0.20", "T": 1_700_000_000_500, "m": False},
        ]
        http = FakeHttp([HttpResponse(200, righe)])
        broker: BinanceBroker = make_broker(
            load_config(Path("config/config.yaml")),
            Credentials(
                api_key=SecretStr("public"),
                api_secret=SecretStr("public"),
                mode=Mode.TESTNET,
            ),
            http,
        )
        trade = await broker.agg_trades("BTCUSDT", start_ms=1_700_000_000_000)

        assert [t.trade_id for t in trade] == [100, 101]
        assert trade[0].is_buyer_maker is True
        assert trade[1].quantity == Decimal("0.20")
        assert "/api/v3/aggTrades?" in http.requests[0][1]
        assert "signature=" not in http.requests[0][1], (
            "e' un endpoint pubblico: non si firma e non si usano chiavi vere"
        )

    @pytest.mark.asyncio
    async def test_la_paginazione_riparte_dallidentificativo(self) -> None:
        """Due trade nello stesso millisecondo: ripartire dal tempo li
        prenderebbe due volte o ne salterebbe uno."""
        from execution.binance import BinanceBroker, HttpResponse
        from tests.test_execution import FakeHttp, make_broker

        http = FakeHttp([HttpResponse(200, [])])
        broker: BinanceBroker = make_broker(
            load_config(Path("config/config.yaml")),
            Credentials(
                api_key=SecretStr("public"),
                api_secret=SecretStr("public"),
                mode=Mode.TESTNET,
            ),
            http,
        )
        await broker.agg_trades("BTCUSDT", from_id=500)
        assert "fromId=500" in http.requests[0][1]
        assert "startTime" not in http.requests[0][1]

        with pytest.raises(Exception, match="start_ms oppure from_id"):
            await broker.agg_trades("BTCUSDT")


class TestScaricoCheFinisce:
    """Due giorni di flusso ordini su BTCUSDT sono qualche milione di
    scambi: a mille per richiesta sono migliaia di chiamate, e con il
    throttle diventano decine di minuti. Dalla finestra sembra bloccato,
    e in pratica lo e'. Serve un tetto, e serve che si veda il progresso."""

    @pytest.mark.asyncio
    async def test_il_tetto_ferma_lo_scarico(self) -> None:
        from execution.binance import HttpResponse
        from scripts.fetch_history import _scarica_flusso
        from tests.test_execution import FakeHttp, make_broker

        pagina = [
            {"a": i, "p": "60000", "q": "0.1", "T": 1_700_000_000_000 + i, "m": False}
            for i in range(1000)
        ]
        http = FakeHttp([HttpResponse(200, pagina)])
        broker = make_broker(
            load_config(Path("config/config.yaml")),
            Credentials(
                api_key=SecretStr("public"),
                api_secret=SecretStr("public"),
                mode=Mode.TESTNET,
            ),
            http,
        )

        class StoreFinto:
            def __init__(self) -> None:
                self.scritti = 0

            def add_trades(self, trade: Sequence[object]) -> None:
                self.scritti += len(trade)

        store = StoreFinto()
        totale = await _scarica_flusso(
            broker,
            store,  # type: ignore[arg-type]
            ["BTCUSDT"],
            ore=48,
            fine_ms=1_700_000_000_000 + 10**9,
            massimo_per_simbolo=2_500,
        )
        assert totale <= 2_500, "senza tetto questo ciclo non finirebbe mai"
        assert len(http.requests) <= 4

    @pytest.mark.asyncio
    async def test_riferisce_il_progresso(self) -> None:
        from execution.binance import HttpResponse
        from scripts.fetch_history import _scarica_flusso
        from tests.test_execution import FakeHttp, make_broker

        pagina = [
            {"a": i, "p": "60000", "q": "0.1", "T": 1_700_000_000_000 + i, "m": False}
            for i in range(1000)
        ]
        http = FakeHttp([HttpResponse(200, pagina)])
        broker = make_broker(
            load_config(Path("config/config.yaml")),
            Credentials(
                api_key=SecretStr("public"),
                api_secret=SecretStr("public"),
                mode=Mode.TESTNET,
            ),
            http,
        )

        class StoreFinto:
            def add_trades(self, trade: Sequence[object]) -> None: ...

        avanzamenti: list[str] = []
        await _scarica_flusso(
            broker,
            StoreFinto(),  # type: ignore[arg-type]
            ["BTCUSDT"],
            ore=1,
            fine_ms=1_700_000_000_000 + 10**9,
            massimo_per_simbolo=3_000,
            progresso=avanzamenti.append,
        )
        assert avanzamenti, "senza progresso l'utente vede solo i secondi passare"
        assert any("BTCUSDT" in m for m in avanzamenti)
