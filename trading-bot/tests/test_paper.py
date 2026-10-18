"""Modalita' paper: mercato vero, ordini finti, nessuna chiave.

La testnet di Binance e' un mercato quasi deserto: i prezzi esistono ma
gli scambi sono pochissimi, e il bot decide guardando proprio il flusso
degli scambi. Provarlo li' dice poco, e chiede comunque di generare delle
chiavi.

In paper il bot legge i dati pubblici del mercato vero -- gli stessi che
vede chiunque apra un grafico -- e simula le esecuzioni con lo stesso
modello di costi del backtest. Niente chiavi, niente ordini autenticati,
niente denaro: e' l'unico modo di vedere come si comporterebbe davvero
senza rischiare nulla.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from config.credentials import load_credentials
from config.schema import Mode, load_config
from execution.binance import BinanceBroker
from execution.broker import PaperBroker
from runner import build_broker

D = Decimal


@pytest.fixture
def config_paper() -> object:
    config = load_config(Path("config/config.yaml"))
    raw = config.model_dump()
    raw["mode"] = "paper"
    return type(config).model_validate(raw)


class TestLaModalitaEsiste:
    def test_paper_non_e_denaro_vero(self) -> None:
        assert Mode.PAPER.uses_real_money is False

    def test_paper_non_invia_ordini_allexchange(self) -> None:
        """E' la proprieta' che rende la modalita' sicura: qualunque cosa
        decida il bot, dall'exchange non passa nessun ordine."""
        assert Mode.PAPER.touches_exchange is False

    def test_paper_legge_pero_dati_veri(self) -> None:
        """A differenza del backtest, i dati arrivano dal mercato adesso."""
        assert Mode.PAPER.usa_dati_dal_vivo is True
        assert Mode.BACKTEST.usa_dati_dal_vivo is False
        assert Mode.TESTNET.usa_dati_dal_vivo is True


class TestNienteChiavi:
    def test_non_chiede_credenziali(self) -> None:
        assert load_credentials(Mode.PAPER) is None

    def test_il_broker_e_simulato(self, config_paper: object) -> None:
        from core.clock import SimulatedClock

        broker = build_broker(config_paper, SimulatedClock(0), None)  # type: ignore[arg-type]
        assert isinstance(broker, PaperBroker)
        assert not isinstance(broker, BinanceBroker)

    def test_il_capitale_simulato_e_quello_dichiarato(
        self, config_paper: object
    ) -> None:
        from core.clock import SimulatedClock

        broker = build_broker(config_paper, SimulatedClock(0), None)  # type: ignore[arg-type]
        saldo = broker.balances["USDT"]  # type: ignore[attr-defined]
        assert saldo == config_paper.capital_quote  # type: ignore[attr-defined]


class TestFlussoDalVivo:
    def test_usa_lendpoint_pubblico_di_produzione(self, config_paper: object) -> None:
        """I prezzi devono essere quelli veri: e' tutto il punto della
        modalita'. L'endpoint e' pubblico, non serve autenticarsi."""
        assert "stream.binance.com" in config_paper.ws_url  # type: ignore[attr-defined]
        assert "testnet" not in config_paper.ws_url  # type: ignore[attr-defined]

    def test_lurl_rest_e_quello_pubblico_di_produzione(
        self, config_paper: object
    ) -> None:
        assert "api.binance.com" in config_paper.rest_url  # type: ignore[attr-defined]


class TestGuardieRimaste:
    def test_la_modalita_live_resta_dietro_la_sua_guardia(self) -> None:
        """Aggiungere una modalita' non deve aver aperto una porta."""
        from config.credentials import CredentialsError

        with pytest.raises(CredentialsError):
            load_credentials(Mode.LIVE)


class TestFiltriVeriEPrezziVivi:
    """Le regole dell'exchange (lotto minimo, controvalore minimo) vanno
    prese da Binance anche in paper: inventarle vorrebbe dire simulare
    ordini che nel mondo vero sarebbero stati rifiutati, e la prova non
    direbbe niente. L'endpoint e' pubblico, non serve firmare."""

    @pytest.mark.asyncio
    async def test_i_filtri_arrivano_dallendpoint_pubblico(self) -> None:
        from core.clock import SimulatedClock
        from execution.binance import HttpResponse
        from execution.paper_live import PaperLiveBroker
        from tests.test_execution import EXCHANGE_INFO, FakeHttp

        http = FakeHttp([HttpResponse(200, EXCHANGE_INFO)])
        broker = PaperLiveBroker.costruisci(
            config=load_config(Path("config/config.yaml")),
            clock=SimulatedClock(0),
            http=http,
        )
        filtri = await broker.exchange_filters()

        assert "BTCUSDT" in filtri
        assert filtri["BTCUSDT"].min_notional > 0
        assert "signature=" not in http.requests[0][1], "endpoint pubblico, non si firma"

    @pytest.mark.asyncio
    async def test_le_esecuzioni_restano_simulate(self) -> None:
        """Il punto della modalita': i dati entrano, gli ordini no."""
        from core.clock import SimulatedClock
        from core.types import Order, OrderType, Side
        from execution.binance import HttpResponse
        from execution.paper_live import PaperLiveBroker
        from tests.test_execution import EXCHANGE_INFO, FakeHttp

        http = FakeHttp([HttpResponse(200, EXCHANGE_INFO)])
        broker = PaperLiveBroker.costruisci(
            config=load_config(Path("config/config.yaml")),
            clock=SimulatedClock(0),
            http=http,
        )
        await broker.exchange_filters()
        broker.set_price("BTCUSDT", D("60000"))
        richieste_prima = len(http.requests)

        ack = await broker.submit(
            Order(
                client_order_id="paper-1",
                symbol="BTCUSDT",
                side=Side.BUY,
                order_type=OrderType.MARKET,
                quantity=D("0.001"),
                created_at_ms=0,
            )
        )

        assert ack.filled_quantity == D("0.001")
        assert len(http.requests) == richieste_prima, (
            "un ordine in paper non deve produrre nessuna chiamata all'exchange"
        )
