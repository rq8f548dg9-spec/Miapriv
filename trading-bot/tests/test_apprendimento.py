"""Apprendimento: esperimenti verificabili, promozioni motivate.

Il modo normale di "far migliorare" un bot e' provare cento varianti di
parametri sugli stessi dati e tenere quella che ha reso di piu'. E' il
modo normale di ottenere un bot che ha imparato a memoria il passato: con
cento tentativi, la migliore sembra brava anche se tutte sono inutili.

Qui una variante viene promossa solo se regge su piu' finestre
temporali separate, non se vince in media. E non viene mai applicata da
sola: viene proposta, con i numeri accanto, e resta all'utente decidere.
Un bot che si cambia i parametri da solo mentre opera e' un bot di cui
nessuno sa piu' cosa stia facendo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apprendimento.esperimenti import (
    Candidato,
    RegistroEsperimenti,
    valuta_candidati,
)
from config.schema import AppConfig, load_config
from tests.test_no_lookahead import AlwaysTradingGenerator, synthetic_candles


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


class TestCandidati:
    def test_un_candidato_descrive_solo_cosa_cambia(self) -> None:
        candidato = Candidato(
            nome="soglia piu' bassa",
            motivo="verificare se il vantaggio piccolo paga",
            modifiche={"signals.min_probability": 0.52},
        )
        assert candidato.modifiche == {"signals.min_probability": 0.52}

    def test_un_candidato_non_puo_toccare_le_protezioni(self) -> None:
        """La ricerca dei parametri non e' il posto da cui si allentano
        stop e limiti: se un parametro di rischio va cambiato, lo cambia
        una persona guardandolo in faccia."""
        with pytest.raises(ValueError, match="protezion"):
            Candidato(
                nome="piu' leva",
                motivo="vediamo",
                modifiche={"risk.killswitch.max_daily_drawdown_pct": 0.5},
            )

    def test_un_percorso_inesistente_viene_rifiutato_subito(self) -> None:
        with pytest.raises(ValueError, match="sconosciuto"):
            Candidato(nome="x", motivo="y", modifiche={"signals.inventato": 1})


class TestValutazione:
    def test_ogni_candidato_viene_misurato_su_piu_finestre(
        self, config: AppConfig
    ) -> None:
        # Finestre corte: il file di produzione ne usa da trenta giorni,
        # che su un campione sintetico non si chiudono mai.
        raw = config.model_dump()
        raw["backtest"]["walkforward"] = {
            "train_days": 2, "test_days": 1, "step_days": 2,
            "purge_bars": 10, "embargo_bars": 10, "min_trades_per_window": 1,
        }
        config = type(config).model_validate(raw)

        candele = synthetic_candles(9_000)
        esiti = valuta_candidati(
            config,
            candele,
            [Candidato("piu' attivo", "prova", {"signals.min_probability": 0.52})],
            generatore=lambda _cfg: AlwaysTradingGenerator(),
        )
        assert len(esiti) == 1
        esito = esiti[0]
        assert esito.finestre_valutate >= 1
        assert esito.coerenza is None or 0.0 <= esito.coerenza <= 1.0

    def test_la_promozione_chiede_coerenza_non_media(self, config: AppConfig) -> None:
        """Una variante che vince una finestra su cinque e perde nelle
        altre quattro puo' avere una media positiva e non essere una
        strategia: e' una scommessa andata bene una volta."""
        from apprendimento.esperimenti import EsitoCandidato, decidi_promozione

        vincente_per_caso = EsitoCandidato(
            candidato=Candidato("a", "b", {"signals.min_probability": 0.52}),
            finestre_valutate=5,
            finestre_in_profitto=1,
            coerenza=0.2,
            rendimento_medio=0.05,
            operazioni=120,
            calo_peggiore=0.1,
        )
        assert decidi_promozione(vincente_per_caso).promosso is False
        assert "finestre" in decidi_promozione(vincente_per_caso).motivo

    def test_la_promozione_chiede_anche_un_campione(self) -> None:
        from apprendimento.esperimenti import EsitoCandidato, decidi_promozione

        poche_operazioni = EsitoCandidato(
            candidato=Candidato("a", "b", {"signals.min_probability": 0.52}),
            finestre_valutate=4,
            finestre_in_profitto=4,
            coerenza=1.0,
            rendimento_medio=0.08,
            operazioni=6,
            calo_peggiore=0.02,
        )
        decisione = decidi_promozione(poche_operazioni)
        assert decisione.promosso is False
        assert "operazioni" in decisione.motivo

    def test_una_variante_solida_viene_proposta(self) -> None:
        from apprendimento.esperimenti import EsitoCandidato, decidi_promozione

        solida = EsitoCandidato(
            candidato=Candidato("a", "b", {"signals.min_probability": 0.52}),
            finestre_valutate=5,
            finestre_in_profitto=4,
            coerenza=0.8,
            rendimento_medio=0.04,
            operazioni=140,
            calo_peggiore=0.05,
        )
        decisione = decidi_promozione(solida)
        assert decisione.promosso is True
        assert decisione.motivo


class TestRegistro:
    def test_gli_esperimenti_restano_scritti(self, tmp_path: Path) -> None:
        from apprendimento.esperimenti import EsitoCandidato

        registro = RegistroEsperimenti(tmp_path / "esperimenti.jsonl")
        registro.annota(
            EsitoCandidato(
                candidato=Candidato("a", "b", {"signals.min_probability": 0.52}),
                finestre_valutate=3,
                finestre_in_profitto=2,
                coerenza=0.67,
                rendimento_medio=0.01,
                operazioni=40,
                calo_peggiore=0.03,
            ),
            impronta_dati="abc123",
            ts_ms=1_700_000_000_000,
        )
        righe = (tmp_path / "esperimenti.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(righe) == 1
        scritto = json.loads(righe[0])
        assert scritto["candidato"] == "a"
        assert scritto["impronta_dati"] == "abc123"
        assert "promosso" in scritto

    def test_la_storia_si_rilegge(self, tmp_path: Path) -> None:
        from apprendimento.esperimenti import EsitoCandidato

        percorso = tmp_path / "esperimenti.jsonl"
        registro = RegistroEsperimenti(percorso)
        for i in range(3):
            registro.annota(
                EsitoCandidato(
                    candidato=Candidato(
                        f"prova {i}", "b", {"signals.min_probability": 0.52}
                    ),
                    finestre_valutate=3,
                    finestre_in_profitto=i,
                    coerenza=i / 3,
                    rendimento_medio=0.01 * i,
                    operazioni=40,
                    calo_peggiore=0.03,
                ),
                impronta_dati="abc",
                ts_ms=1_700_000_000_000 + i,
            )
        assert len(RegistroEsperimenti(percorso).storia()) == 3

    def test_lo_stesso_esperimento_sugli_stessi_dati_non_si_ripete(
        self, tmp_path: Path
    ) -> None:
        """Rifare la stessa misura sugli stessi dati non aggiunge
        informazione, ma rifatta abbastanza volte prima o poi "vince":
        e' il modo piu' facile di ingannarsi."""
        from apprendimento.esperimenti import EsitoCandidato

        registro = RegistroEsperimenti(tmp_path / "esperimenti.jsonl")
        esito = EsitoCandidato(
            candidato=Candidato("a", "b", {"signals.min_probability": 0.52}),
            finestre_valutate=3,
            finestre_in_profitto=3,
            coerenza=1.0,
            rendimento_medio=0.02,
            operazioni=60,
            calo_peggiore=0.03,
        )
        assert registro.gia_provato(esito.candidato, "abc") is False
        registro.annota(esito, impronta_dati="abc", ts_ms=1)
        assert registro.gia_provato(esito.candidato, "abc") is True
        assert registro.gia_provato(esito.candidato, "dati-nuovi") is False


class TestNienteApplicazioneAutomatica:
    def test_il_modulo_non_scrive_la_configurazione(self) -> None:
        """Il bot propone, la persona dispone. Un bot che si cambia i
        parametri da solo mentre opera e' un bot di cui nessuno sa piu'
        cosa stia facendo."""
        sorgente = Path("apprendimento/esperimenti.py").read_text(encoding="utf-8")
        assert "write_text" not in sorgente.replace("# ", "")
        assert "config.yaml" not in sorgente
