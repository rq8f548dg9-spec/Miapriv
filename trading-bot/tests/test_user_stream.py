"""Flusso dati dell'account: la fonte autorevole delle esecuzioni.

Finora i fill si scoprivano chiedendo: alla risposta dell'ordine e poi
alla riconciliazione periodica. Funziona, ma lascia una finestra in cui
una posizione esiste sull'exchange e non nei libri del bot, ed e'
proprio la finestra in cui un crash fa danno.

Il flusso dell'account li annuncia appena avvengono. Dal 2025 Binance ha
tolto dalla documentazione il vecchio ``listenKey`` su stream.binance.com
e indica come strada preferita la sottoscrizione firmata sulla
WebSocket API (``userDataStream.subscribe.signature``): e' quella
implementata qui.

ATTENZIONE, ed e' scritto anche nel modulo: la stretta di mano con
l'endpoint vero non e' stata provata, perche' richiede credenziali e una
connessione autenticata. Quello che questi test verificano e' la forma
dei messaggi e il trattamento degli eventi, non che Binance risponda.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from core.types import Fill
from execution.user_stream import (
    FlussoAccount,
    costruisci_sottoscrizione,
    interpreta_evento,
)

D = Decimal
NOW = 1_700_000_000_000


class TestSottoscrizione:
    def test_usa_il_metodo_nuovo_non_il_listen_key(self) -> None:
        richiesta = costruisci_sottoscrizione(
            api_key="CHIAVE", api_secret="SEGRETO", ts_ms=NOW
        )
        assert richiesta["method"] == "userDataStream.subscribe.signature"
        assert "listenKey" not in json.dumps(richiesta)

    def test_la_firma_copre_i_parametri_ordinati(self) -> None:
        import hashlib
        import hmac

        richiesta = costruisci_sottoscrizione(
            api_key="CHIAVE", api_secret="SEGRETO", ts_ms=NOW
        )
        params = dict(richiesta["params"])
        firma = params.pop("signature")
        atteso = hmac.new(
            b"SEGRETO",
            "&".join(f"{k}={v}" for k, v in sorted(params.items())).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert firma == atteso

    def test_il_segreto_non_compare_nella_richiesta(self) -> None:
        richiesta = costruisci_sottoscrizione(
            api_key="CHIAVE", api_secret="SEGRETISSIMO", ts_ms=NOW
        )
        assert "SEGRETISSIMO" not in json.dumps(richiesta)

    def test_ogni_richiesta_ha_un_identificativo_suo(self) -> None:
        prima = costruisci_sottoscrizione(api_key="A", api_secret="B", ts_ms=NOW)
        dopo = costruisci_sottoscrizione(api_key="A", api_secret="B", ts_ms=NOW)
        assert prima["id"] != dopo["id"]


def _esecuzione(**override: object) -> dict[str, object]:
    base: dict[str, object] = {
        "e": "executionReport",
        "s": "BTCUSDT",
        "c": "tb-1",
        "S": "BUY",
        "x": "TRADE",
        "X": "PARTIALLY_FILLED",
        "i": 777,
        "l": "0.00100000",
        "L": "60000.00",
        "n": "0.00000100",
        "N": "BTC",
        "T": NOW,
        "t": 12345,
        "m": False,
    }
    base.update(override)
    return base


class TestInterpretazione:
    def test_un_executionreport_diventa_un_fill(self) -> None:
        fill = interpreta_evento(_esecuzione())
        assert fill is not None
        assert fill.symbol == "BTCUSDT"
        assert fill.quantity == D("0.001")
        assert fill.price == D("60000.00")
        assert fill.fee == D("0.000001")
        assert fill.fee_asset == "BTC"
        assert fill.trade_id == "12345"
        assert fill.client_order_id == "tb-1"
        assert fill.exchange_order_id == "777"

    def test_levento_avvolto_viene_srotolato(self) -> None:
        """Alcuni endpoint consegnano {subscriptionId, event}: un parser
        che non lo sa butta via proprio le esecuzioni."""
        fill = interpreta_evento({"subscriptionId": 1, "event": _esecuzione()})
        assert fill is not None
        assert fill.trade_id == "12345"

    def test_un_evento_che_non_e_unesecuzione_viene_ignorato(self) -> None:
        assert interpreta_evento(_esecuzione(x="NEW", l="0")) is None
        assert interpreta_evento({"e": "outboundAccountPosition"}) is None
        assert interpreta_evento({"e": "executionReport", "x": "CANCELED"}) is None

    def test_una_quantita_nulla_non_e_unesecuzione(self) -> None:
        assert interpreta_evento(_esecuzione(l="0.00000000")) is None

    def test_un_messaggio_malformato_non_solleva(self) -> None:
        assert interpreta_evento({"e": "executionReport", "x": "TRADE"}) is None
        assert interpreta_evento({}) is None

    def test_il_lato_venditore_resta_venditore(self) -> None:
        fill = interpreta_evento(_esecuzione(S="SELL"))
        assert fill is not None
        assert fill.side.value == "SELL"


class TestFlusso:
    @pytest.mark.asyncio
    async def test_consegna_le_esecuzioni_al_destinatario(self) -> None:
        ricevuti: list[Fill] = []
        messaggi = [
            json.dumps({"result": None, "id": "x"}),
            json.dumps(_esecuzione()),
            json.dumps(_esecuzione(t=12346, l="0.00200000")),
        ]

        flusso = FlussoAccount(
            api_key="A",
            api_secret="B",
            on_fill=ricevuti.append,
            url="wss://esempio.invalid/ws-api/v3",
        )
        for messaggio in messaggi:
            flusso.gestisci_messaggio(messaggio)

        assert [f.trade_id for f in ricevuti] == ["12345", "12346"]
        assert flusso.esecuzioni_ricevute == 2

    @pytest.mark.asyncio
    async def test_un_messaggio_illeggibile_non_ferma_il_flusso(self) -> None:
        ricevuti: list[Fill] = []
        flusso = FlussoAccount(
            api_key="A", api_secret="B", on_fill=ricevuti.append,
            url="wss://esempio.invalid/ws-api/v3",
        )
        flusso.gestisci_messaggio("{non json")
        flusso.gestisci_messaggio(json.dumps(_esecuzione()))
        assert len(ricevuti) == 1
        assert flusso.messaggi_illeggibili == 1

    @pytest.mark.asyncio
    async def test_un_errore_dallexchange_viene_segnalato_non_ingoiato(self) -> None:
        flusso = FlussoAccount(
            api_key="A", api_secret="B", on_fill=lambda _f: None,
            url="wss://esempio.invalid/ws-api/v3",
        )
        flusso.gestisci_messaggio(
            json.dumps({"id": "x", "status": 401, "error": {"code": -1022, "msg": "bad sig"}})
        )
        assert flusso.ultimo_errore is not None
        assert "-1022" in flusso.ultimo_errore


class TestNienteChiaviNeiLog:
    def test_lurl_non_contiene_la_firma(self) -> None:
        """La firma va nel corpo del messaggio, non nella query: gli URL
        finiscono nei log degli errori molto piu' facilmente."""
        flusso = FlussoAccount(
            api_key="A", api_secret="B", on_fill=lambda _f: None,
            url="wss://esempio.invalid/ws-api/v3",
        )
        assert "signature" not in flusso.url
        assert parse_qs(flusso.url.split("?")[-1]) == {} or "?" not in flusso.url


class TestCollegamentoAlBot:
    """Collegato al bot, il flusso deve restare spento dove non serve e
    consegnare le esecuzioni dove serve, senza mai diventare l'unica
    fonte: la riconciliazione resta."""

    def test_spento_in_paper(self, tmp_path: object) -> None:
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        bot = make_bot(config, tmp_path)  # type: ignore[arg-type]
        assert config.mode.value == "paper"
        assert bot.flusso_account is None, (
            "in paper non esiste un account da ascoltare"
        )

    def test_le_esecuzioni_dal_flusso_entrano_nella_contabilita(
        self, tmp_path: object
    ) -> None:
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        raw = config.model_dump()
        raw["mode"] = "testnet"
        bot = make_bot(type(config).model_validate(raw), tmp_path)  # type: ignore[arg-type]

        flusso = FlussoAccount(
            api_key="A", api_secret="B", on_fill=bot.apply_fill,
            url="wss://esempio.invalid/ws-api/v3",
        )
        flusso.gestisci_messaggio(json.dumps(_esecuzione()))

        # 0,001 comprati meno 0,000001 di commissione pagata in BTC: la
        # commissione sull'asset ricevuto riduce quello che si possiede
        # davvero, e il portafoglio lo sa.
        atteso = D("0.001") - D("0.000001")
        assert bot.portfolio.positions["BTCUSDT"].quantity == atteso

        # Lo stesso fill in arrivo due volte non raddoppia niente: il
        # flusso aggiunge informazione, non la duplica.
        flusso.gestisci_messaggio(json.dumps(_esecuzione()))
        assert bot.portfolio.positions["BTCUSDT"].quantity == atteso
