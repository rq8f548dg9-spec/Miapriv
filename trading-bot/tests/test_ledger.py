"""Registro a eventi del patrimonio.

Il bot doveva ripartire sempre dal capitale scritto in configurazione: se
cento diventavano centotrenta, al riavvio tornava a dimensionare su cento,
e il guadagno restava fermo. Serve un posto dove la differenza fra "mi
hanno dato altri soldi" e "ho guadagnato dei soldi" sia scritta, perche'
confonderle fa sembrare un versamento un profitto.

Il registro e' la verita' su quanto capitale e' in gestione. Non e' la
verita' sui saldi: quella resta sull'exchange, e il bot prende sempre il
minore fra i due.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from core.ledger import EventKind, EventLedger, LedgerError
from core.types import Fill, Side

D = Decimal

NOW = 1_700_000_000_000


def fill(
    trade_id: str,
    *,
    side: Side = Side.BUY,
    price: str = "100",
    quantity: str = "1",
    fee: str = "0.1",
    fee_asset: str = "USDT",
    symbol: str = "BTCUSDT",
) -> Fill:
    return Fill(
        client_order_id=f"ord-{trade_id}",
        symbol=symbol,
        side=side,
        price=D(price),
        quantity=D(quantity),
        fee=D(fee),
        fee_asset=fee_asset,
        ts_ms=NOW,
        is_maker=False,
        trade_id=trade_id,
    )


class TestScritturaERilettura:
    def test_un_registro_nuovo_e_vuoto(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        assert ledger.is_empty
        assert ledger.events() == []

    def test_gli_eventi_sopravvivono_al_riavvio(self, tmp_path: Path) -> None:
        percorso = tmp_path / "ledger.jsonl"
        primo = EventLedger(percorso)
        primo.conferimento(D("100"), ts_ms=NOW, note="versamento iniziale")
        primo.record_fill(fill("1"))

        secondo = EventLedger(percorso)
        eventi = secondo.events()
        assert [e.kind for e in eventi] == [EventKind.CONFERIMENTO, EventKind.ESECUZIONE]
        assert [e.seq for e in eventi] == [1, 2]
        assert eventi[0].note == "versamento iniziale"

    def test_la_sequenza_non_salta_e_non_torna_indietro(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        for i in range(5):
            ledger.conferimento(D("10"), ts_ms=NOW + i)
        assert [e.seq for e in ledger.events()] == [1, 2, 3, 4, 5]

    def test_una_riga_tronca_in_fondo_si_scarta(self, tmp_path: Path) -> None:
        """Un crash a meta' scrittura lascia l'ultima riga a meta'. Quel
        movimento non e' mai stato completato: scartarlo e' corretto,
        rifiutarsi di partire per sempre no."""
        percorso = tmp_path / "ledger.jsonl"
        ledger = EventLedger(percorso)
        ledger.conferimento(D("100"), ts_ms=NOW)
        with percorso.open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 2, "kind": "confer')

        ripreso = EventLedger(percorso)
        assert len(ripreso.events()) == 1
        ripreso.conferimento(D("50"), ts_ms=NOW + 1)
        assert [e.seq for e in EventLedger(percorso).events()] == [1, 2]

    def test_una_riga_rotta_in_mezzo_blocca_lavvio(self, tmp_path: Path) -> None:
        """Qui non c'e' niente da indovinare: manca un movimento di denaro
        in mezzo agli altri, e il totale che ne uscirebbe sarebbe falso."""
        percorso = tmp_path / "ledger.jsonl"
        ledger = EventLedger(percorso)
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.conferimento(D("50"), ts_ms=NOW + 1)
        righe = percorso.read_text(encoding="utf-8").splitlines()
        righe[0] = '{"rotta": true'
        percorso.write_text("\n".join(righe) + "\n", encoding="utf-8")

        with pytest.raises(LedgerError, match="riga 1"):
            EventLedger(percorso)

    def test_una_sequenza_fuori_ordine_blocca_lavvio(self, tmp_path: Path) -> None:
        percorso = tmp_path / "ledger.jsonl"
        ledger = EventLedger(percorso)
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.conferimento(D("50"), ts_ms=NOW + 1)
        righe = percorso.read_text(encoding="utf-8").splitlines()
        righe[1] = righe[1].replace('"seq": 2', '"seq": 7')
        percorso.write_text("\n".join(righe) + "\n", encoding="utf-8")

        with pytest.raises(LedgerError, match="sequenza"):
            EventLedger(percorso)


class TestConferimentiEPrelievi:
    def test_un_conferimento_non_e_un_guadagno(self, tmp_path: Path) -> None:
        """E' la distinzione per cui esiste questo registro."""
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.conferimento(D("50"), ts_ms=NOW + 1)

        stato = ledger.replay()
        assert stato.conferito == D("150")
        assert stato.prelevato == D("0")
        assert stato.pnl_netto == D("0"), "nessuna operazione, nessun guadagno"
        assert stato.capitale_in_gestione == D("150")

    def test_un_prelievo_non_e_una_perdita(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.prelievo(D("30"), ts_ms=NOW + 1)

        stato = ledger.replay()
        assert stato.prelevato == D("30")
        assert stato.pnl_netto == D("0")
        assert stato.capitale_in_gestione == D("70")

    def test_non_si_preleva_piu_di_quanto_c_e(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        with pytest.raises(LedgerError, match="liquidi"):
            ledger.prelievo(D("150"), ts_ms=NOW + 1)
        assert len(ledger.events()) == 1, "un prelievo rifiutato non si scrive"

    def test_importi_non_positivi_rifiutati(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        with pytest.raises(LedgerError):
            ledger.conferimento(D("0"), ts_ms=NOW)
        with pytest.raises(LedgerError):
            ledger.prelievo(D("-5"), ts_ms=NOW)


class TestReinvestimento:
    def test_il_guadagno_alza_il_capitale_in_gestione(self, tmp_path: Path) -> None:
        """Cento che diventano centotrenta sono centotrenta: e' il punto."""
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.record_fill(fill("1", side=Side.BUY, price="100", quantity="1", fee="0"))
        ledger.record_fill(fill("2", side=Side.SELL, price="130", quantity="1", fee="0"))

        stato = ledger.replay()
        assert stato.conferito == D("100")
        assert stato.pnl_netto == D("30")
        assert stato.capitale_in_gestione == D("130")
        assert stato.portfolio.cash == D("130")

    def test_la_perdita_abbassa_il_capitale_in_gestione(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.record_fill(fill("1", side=Side.BUY, price="100", quantity="1", fee="0"))
        ledger.record_fill(fill("2", side=Side.SELL, price="80", quantity="1", fee="0"))

        stato = ledger.replay()
        assert stato.pnl_netto == D("-20")
        assert stato.capitale_in_gestione == D("80")

    def test_le_commissioni_entrano_nel_risultato(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.record_fill(fill("1", side=Side.BUY, price="100", quantity="1", fee="0.5"))
        ledger.record_fill(fill("2", side=Side.SELL, price="130", quantity="1", fee="0.5"))

        stato = ledger.replay()
        assert stato.commissioni == D("1")
        assert stato.pnl_netto == D("29")
        assert stato.capitale_in_gestione == D("129")

    def test_una_posizione_aperta_resta_nel_capitale(self, tmp_path: Path) -> None:
        """Comprare non e' perdere: il contante scende, il patrimonio no."""
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        ledger.record_fill(fill("1", side=Side.BUY, price="100", quantity="0.5", fee="0"))

        stato = ledger.replay()
        assert stato.portfolio.cash == D("50")
        assert stato.portfolio.positions["BTCUSDT"].quantity == D("0.5")
        assert stato.capitale_in_gestione == D("100"), (
            "al prezzo di carico il patrimonio non e' cambiato"
        )

    def test_il_doppione_non_viene_contato_due_volte(self, tmp_path: Path) -> None:
        ledger = EventLedger(tmp_path / "ledger.jsonl")
        ledger.conferimento(D("100"), ts_ms=NOW)
        assert ledger.record_fill(fill("1")) is True
        assert ledger.record_fill(fill("1")) is False
        assert len([e for e in ledger.events() if e.kind is EventKind.ESECUZIONE]) == 1


class TestPrimoAvvio:
    def test_il_capitale_dichiarato_apre_il_registro_una_volta_sola(
        self, tmp_path: Path
    ) -> None:
        percorso = tmp_path / "ledger.jsonl"
        primo = EventLedger(percorso)
        assert primo.apri_se_vuoto(D("100"), ts_ms=NOW) is True

        secondo = EventLedger(percorso)
        assert secondo.apri_se_vuoto(D("999"), ts_ms=NOW + 1) is False, (
            "il capitale in configurazione non puo' riscrivere la storia"
        )
        assert secondo.replay().conferito == D("100")


class TestVersamentiNelPortafoglio:
    """Un versamento alza il contante senza essere un guadagno, e senza
    cancellare una perdita in corso: se spostasse i riferimenti del
    drawdown, bastterebbe aggiungere denaro per far sparire un calo e
    disarmare le protezioni proprio mentre servono."""

    def test_il_versamento_non_cancella_il_calo_in_corso(self, tmp_path: Path) -> None:
        from core.portfolio import Portfolio

        portfolio = Portfolio(D("100"))
        portfolio.cash = D("80")  # dopo una perdita
        portfolio._update_peak()
        prima = portfolio.snapshot(NOW).total_drawdown_pct
        assert prima > 0

        portfolio.deposita(D("20"))

        assert portfolio.cash == D("100")
        # La perdita in euro resta quella: venti versati non sono venti
        # recuperati. In percentuale cala, perche' adesso il capitale e'
        # piu' grande, ed e' corretto: il limite e' una quota del capitale
        # in gestione. Quello che non deve succedere e' che il massimo
        # storico si riallinei all'equity, azzerando il calo.
        assert portfolio.peak_equity - portfolio.equity == D("20")
        assert portfolio.snapshot(NOW).total_drawdown_pct > 0, (
            "un versamento non cancella un calo in corso"
        )
        assert portfolio.realized_pnl == D("0")

    def test_il_prelievo_non_crea_un_calo(self, tmp_path: Path) -> None:
        from core.portfolio import Portfolio

        portfolio = Portfolio(D("100"))
        portfolio.preleva(D("40"))

        assert portfolio.cash == D("60")
        assert portfolio.snapshot(NOW).total_drawdown_pct == 0.0
        assert portfolio.snapshot(NOW).daily_drawdown_pct == 0.0
        assert portfolio.realized_pnl == D("0")

    def test_non_si_preleva_il_contante_che_non_c_e(self, tmp_path: Path) -> None:
        from core.portfolio import Portfolio

        portfolio = Portfolio(D("100"))
        with pytest.raises(ValueError, match="liquid"):
            portfolio.preleva(D("101"))
        assert portfolio.cash == D("100")


class TestRegistroCollegatoAlBot:
    """Il punto per cui esiste tutto questo: dopo un riavvio il bot deve
    dimensionare su quello che ha adesso, non su quello che aveva il primo
    giorno."""

    def test_il_bot_apre_il_registro_al_primo_avvio(self, tmp_path: Path) -> None:
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        bot = make_bot(config, tmp_path)
        assert bot.ledger is not None
        assert bot.ledger.replay().conferito == config.capital_quote

    def test_un_esecuzione_finisce_nel_registro_prima_che_nel_portafoglio(
        self, tmp_path: Path
    ) -> None:
        """Se il processo muore fra le due scritture, al riavvio la
        rilettura del registro rimette tutto a posto. Nell'ordine
        contrario l'esecuzione sarebbe persa."""
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        bot = make_bot(config, tmp_path)
        assert bot.ledger is not None

        bot.apply_fill(fill("77", side=Side.BUY, price="60000", quantity="0.001", fee="0"))

        eventi = [e for e in bot.ledger.events() if e.kind is EventKind.ESECUZIONE]
        assert [e.trade_id for e in eventi] == ["77"]

    def test_il_guadagno_sopravvive_al_riavvio(self, tmp_path: Path) -> None:
        """Cento che diventano centotrenta restano centotrenta anche dopo
        aver spento e riacceso: era il difetto da chiudere."""
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        primo = make_bot(config, tmp_path)
        assert primo.ledger is not None
        iniziale = primo.portfolio.equity

        primo.apply_fill(fill("1", side=Side.BUY, price="100", quantity="0.3", fee="0"))
        primo.apply_fill(fill("2", side=Side.SELL, price="200", quantity="0.3", fee="0"))
        cresciuto = primo.portfolio.equity
        assert cresciuto > iniziale

        secondo = make_bot(config, tmp_path, con_guida=False)
        assert secondo.portfolio.equity == cresciuto, (
            "al riavvio il capitale in gestione e' quello corrente, non quello iniziale"
        )
        assert secondo.capitale_in_gestione() == cresciuto


class TestTettoDelCapitale:
    """In testnet il conto di prova e' accreditato con migliaia di unita':
    senza tetto il bot dimensionerebbe su una cifra che non somiglia al
    conto vero. Il tetto pero' non puo' restare il capitale del primo
    giorno, altrimenti il guadagno non si reinveste mai."""

    def test_il_tetto_segue_il_capitale_in_gestione(self, tmp_path: Path) -> None:
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        primo = make_bot(config, tmp_path)
        assert primo.reconciler.max_capital == config.capital_quote

        primo.apply_fill(fill("1", side=Side.BUY, price="100", quantity="0.5", fee="0"))
        primo.apply_fill(fill("2", side=Side.SELL, price="200", quantity="0.5", fee="0"))

        secondo = make_bot(config, tmp_path, con_guida=False)
        atteso = secondo.portfolio.cash
        assert atteso > config.capital_quote
        assert secondo.reconciler.max_capital == atteso, (
            "il tetto deve seguire quello che il bot ha davvero, non il primo deposito"
        )
