"""Scenari probabilistici: cosa puo' succedere, non cosa succedera'.

Un backtest da' una storia sola, quella capitata. La domanda che conta
prima di mettere soldi su una strategia e' diversa: con questo
comportamento, in cento giorni come questi, quanto spesso finisco sotto?
Quanto spesso tocco il limite di perdita giornaliero e il bot si ferma?

Si risponde ricampionando le operazioni gia' osservate, a blocchi, per
non distruggere le serie di vincite e perdite consecutive: le perdite in
fila sono esattamente cio' che fa scattare le protezioni, e un
ricampionamento che le spezza produce scenari piu' rosei del vero.

Quello che NON si fa: produrre percentili quando le operazioni sono
poche. Un intervallo di confidenza su otto operazioni e' un numero che
sembra una misura.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest.scenari import SimulazioneScenari, simula_scenari
from core.portfolio import TradeResult

D = Decimal


def esiti(*valori: str, inizio_ms: int = 1_700_000_000_000) -> list[TradeResult]:
    """Operazioni chiuse con il PnL indicato, in ordine."""
    return [
        TradeResult(
            symbol="BTCUSDT",
            ts_ms=inizio_ms + (i + 1) * 60_000,
            realized_pnl=D(valore),
        )
        for i, valore in enumerate(valori)
    ]


class TestCampioneInsufficiente:
    def test_sotto_la_soglia_non_si_inventano_percentili(self) -> None:
        risultato = simula_scenari(
            esiti("1", "-1", "2"), capitale=D("200"), percorsi=100, seed=1
        )
        assert risultato.affidabile is False
        assert risultato.percentili == {}
        assert "operazioni" in risultato.spiegazione

    def test_nessuna_operazione_non_e_un_errore(self) -> None:
        risultato = simula_scenari([], capitale=D("200"), percorsi=10, seed=1)
        assert risultato.affidabile is False
        assert risultato.percorsi_eseguiti == 0


class TestDistribuzione:
    def _molte(self, quante: int = 60) -> list[TradeResult]:
        # Serie con un vantaggio piccolo ma reale: due su tre vincono 1,
        # una su tre perde 1.
        valori = ["1", "1", "-1"] * (quante // 3)
        return esiti(*valori)

    def test_restituisce_i_percentili_richiesti(self) -> None:
        risultato = simula_scenari(
            self._molte(), capitale=D("200"), percorsi=400, orizzonte=30, seed=7
        )
        assert risultato.affidabile is True
        assert set(risultato.percentili) == {"p05", "p25", "p50", "p75", "p95"}
        valori = [risultato.percentili[k] for k in ("p05", "p25", "p50", "p75", "p95")]
        assert valori == sorted(valori), "i percentili devono essere crescenti"

    def test_e_deterministico_a_parita_di_seme(self) -> None:
        """Uno scenario che cambia a ogni esecuzione non si puo' discutere."""
        argomenti = {"capitale": D("200"), "percorsi": 200, "orizzonte": 20, "seed": 42}
        primo = simula_scenari(self._molte(), **argomenti)  # type: ignore[arg-type]
        secondo = simula_scenari(self._molte(), **argomenti)  # type: ignore[arg-type]
        assert primo.percentili == secondo.percentili
        assert primo.probabilita_perdita == secondo.probabilita_perdita

    def test_una_serie_perdente_da_scenari_perdenti(self) -> None:
        perdenti = esiti(*(["-1", "-1", "1"] * 20))
        risultato = simula_scenari(
            perdenti, capitale=D("200"), percorsi=300, orizzonte=30, seed=3
        )
        assert risultato.probabilita_perdita > 0.8
        assert risultato.percentili["p50"] < 0

    def test_una_serie_vincente_da_scenari_vincenti(self) -> None:
        risultato = simula_scenari(
            self._molte(), capitale=D("200"), percorsi=300, orizzonte=30, seed=3
        )
        assert risultato.probabilita_perdita < 0.4
        assert risultato.percentili["p50"] > 0


class TestProtezioni:
    def test_stima_quanto_spesso_scatta_lo_stop_giornaliero(self) -> None:
        """Serve a sapere se il limite che hai messo ti fermera' una volta
        al mese o tre volte a settimana."""
        altalena = esiti(*(["3", "-4"] * 30))
        risultato = simula_scenari(
            altalena,
            capitale=D("200"),
            percorsi=300,
            orizzonte=40,
            seed=5,
            limite_perdita=0.03,
        )
        assert risultato.probabilita_stop is not None
        assert 0.0 < risultato.probabilita_stop <= 1.0

    def test_senza_limite_la_probabilita_di_stop_non_si_dichiara(self) -> None:
        risultato = simula_scenari(
            esiti(*(["1", "-1"] * 30)), capitale=D("200"), percorsi=100, seed=5
        )
        assert risultato.probabilita_stop is None

    def test_il_calo_peggiore_e_una_distribuzione_non_un_numero(self) -> None:
        risultato = simula_scenari(
            esiti(*(["2", "-3", "1"] * 20)),
            capitale=D("200"),
            percorsi=300,
            orizzonte=30,
            seed=11,
        )
        assert risultato.calo_massimo["p50"] >= 0
        assert risultato.calo_massimo["p95"] >= risultato.calo_massimo["p50"]


class TestBlocchi:
    def test_i_blocchi_conservano_le_serie_consecutive(self) -> None:
        """Con blocchi lunghi uno, tre perdite di fila diventano rare e le
        protezioni sembrano scattare meno di quanto farebbero davvero."""
        a_strisce = esiti(*(["-1"] * 5 + ["1"] * 5) * 6)
        spezzato = simula_scenari(
            a_strisce, capitale=D("200"), percorsi=400, orizzonte=30, seed=2,
            blocco=1, limite_perdita=0.02,
        )
        intero = simula_scenari(
            a_strisce, capitale=D("200"), percorsi=400, orizzonte=30, seed=2,
            blocco=5, limite_perdita=0.02,
        )
        assert intero.probabilita_stop is not None
        assert spezzato.probabilita_stop is not None
        assert intero.probabilita_stop > spezzato.probabilita_stop, (
            "ricampionare a blocchi deve conservare le serie, non spianarle"
        )

    def test_un_blocco_piu_lungo_del_campione_viene_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="blocco"):
            simula_scenari(
                esiti(*(["1"] * 30)), capitale=D("200"), percorsi=10, blocco=50
            )


class TestFormato:
    def test_il_riassunto_e_leggibile(self) -> None:
        risultato = simula_scenari(
            esiti(*(["1", "1", "-1"] * 20)),
            capitale=D("200"),
            percorsi=200,
            orizzonte=30,
            seed=9,
        )
        testo = risultato.to_dict()
        assert testo["affidabile"] is True
        assert "percentili" in testo
        assert isinstance(testo["spiegazione"], str)
        assert isinstance(risultato, SimulazioneScenari)
