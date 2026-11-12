"""Ponte verso l'assistente: osserva, non comanda.

Il vincolo di partenza e' che il bot non deve contenere chiavi di nessun
fornitore di modelli: l'assistente e' quello gia' autorizzato sul
computer (Claude Code), e il collegamento passa da una cartella condivisa,
non da una chiamata di rete fatta dal bot.

Il vincolo piu' importante e' un altro: un consiglio non apre posizioni.
Puo' al massimo fermarne una, e solo se qualcuno ha acceso quella
modalita' di proposito. Un suggerimento che diventa un ordine e' un
sistema in cui nessuno sa piu' chi ha deciso, e qui si muove denaro.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai.ponte import Consiglio, ModalitaPonte, PonteAI

NOW = 1_700_000_000_000


class TestSpentoDiDefault:
    def test_nasce_spento(self, tmp_path: Path) -> None:
        ponte = PonteAI(cartella=tmp_path)
        assert ponte.abilitato is False
        assert ponte.modalita is ModalitaPonte.OMBRA

    def test_spento_non_scrive_niente(self, tmp_path: Path) -> None:
        ponte = PonteAI(cartella=tmp_path)
        assert ponte.chiedi("vale la pena?", {"simbolo": "BTCUSDT"}) is None
        assert list(tmp_path.iterdir()) == []

    def test_spento_non_legge_niente(self, tmp_path: Path) -> None:
        """Anche se qualcuno lascia una risposta nella cartella."""
        (tmp_path / "risposta-abc.json").write_text(
            json.dumps({"id": "abc", "verdetto": "evita", "motivo": "test"}),
            encoding="utf-8",
        )
        ponte = PonteAI(cartella=tmp_path)
        assert ponte.leggi_risposta("abc") is None


class TestDomandaERisposta:
    def _acceso(self, cartella: Path, **kwargs: object) -> PonteAI:
        return PonteAI(cartella=cartella, abilitato=True, **kwargs)  # type: ignore[arg-type]

    def test_la_domanda_finisce_in_un_file_leggibile(self, tmp_path: Path) -> None:
        ponte = self._acceso(tmp_path)
        identificativo = ponte.chiedi(
            "questa apertura ha senso?",
            {"simbolo": "BTCUSDT", "probabilita": 0.61},
            ts_ms=NOW,
        )
        assert identificativo is not None
        file = tmp_path / f"richiesta-{identificativo}.json"
        contenuto = json.loads(file.read_text(encoding="utf-8"))
        assert contenuto["domanda"] == "questa apertura ha senso?"
        assert contenuto["contesto"]["simbolo"] == "BTCUSDT"

    def test_la_domanda_non_contiene_segreti(self, tmp_path: Path) -> None:
        """La cartella e' leggibile da chiunque usi il computer: chiavi e
        saldi nominativi non devono finirci dentro."""
        ponte = self._acceso(tmp_path)
        identificativo = ponte.chiedi(
            "che ne pensi?",
            {"api_key": "SEGRETISSIMA", "simbolo": "BTCUSDT", "api_secret": "X"},
            ts_ms=NOW,
        )
        assert identificativo is not None
        testo = (tmp_path / f"richiesta-{identificativo}.json").read_text(
            encoding="utf-8"
        )
        assert "SEGRETISSIMA" not in testo
        assert "BTCUSDT" in testo

    def test_una_risposta_valida_si_legge(self, tmp_path: Path) -> None:
        ponte = self._acceso(tmp_path)
        identificativo = ponte.chiedi("e allora?", {"simbolo": "BTCUSDT"}, ts_ms=NOW)
        assert identificativo is not None
        (tmp_path / f"risposta-{identificativo}.json").write_text(
            json.dumps(
                {
                    "id": identificativo,
                    "verdetto": "evita",
                    "motivo": "spread troppo largo",
                    "ts_ms": NOW + 1_000,
                }
            ),
            encoding="utf-8",
        )
        consiglio = ponte.leggi_risposta(identificativo, now_ms=NOW + 2_000)
        assert isinstance(consiglio, Consiglio)
        assert consiglio.verdetto == "evita"

    def test_una_risposta_vecchia_viene_ignorata(self, tmp_path: Path) -> None:
        """Un consiglio su un mercato di dieci minuti fa non e' un
        consiglio su questo mercato."""
        ponte = self._acceso(tmp_path, scadenza_s=60)
        identificativo = ponte.chiedi("e allora?", {"simbolo": "BTCUSDT"}, ts_ms=NOW)
        assert identificativo is not None
        (tmp_path / f"risposta-{identificativo}.json").write_text(
            json.dumps({"id": identificativo, "verdetto": "evita", "motivo": "x",
                        "ts_ms": NOW}),
            encoding="utf-8",
        )
        assert ponte.leggi_risposta(identificativo, now_ms=NOW + 600_000) is None

    def test_una_risposta_malformata_non_ferma_il_bot(self, tmp_path: Path) -> None:
        ponte = self._acceso(tmp_path)
        identificativo = ponte.chiedi("e allora?", {"simbolo": "BTCUSDT"}, ts_ms=NOW)
        assert identificativo is not None
        (tmp_path / f"risposta-{identificativo}.json").write_text(
            "{non json", encoding="utf-8"
        )
        assert ponte.leggi_risposta(identificativo, now_ms=NOW + 1) is None

    def test_un_verdetto_inventato_viene_scartato(self, tmp_path: Path) -> None:
        ponte = self._acceso(tmp_path)
        identificativo = ponte.chiedi("e allora?", {"simbolo": "BTCUSDT"}, ts_ms=NOW)
        assert identificativo is not None
        (tmp_path / f"risposta-{identificativo}.json").write_text(
            json.dumps({"id": identificativo, "verdetto": "compra-tutto",
                        "motivo": "fidati", "ts_ms": NOW}),
            encoding="utf-8",
        )
        assert ponte.leggi_risposta(identificativo, now_ms=NOW + 1) is None


class TestNonPuoAprire:
    """Il vincolo che conta. Un consiglio non apre niente, mai."""

    def _consiglio(self, verdetto: str) -> Consiglio:
        return Consiglio(id="x", verdetto=verdetto, motivo="perche' si'", ts_ms=NOW)

    def test_in_ombra_nessun_consiglio_cambia_la_decisione(self, tmp_path: Path) -> None:
        ponte = PonteAI(cartella=tmp_path, abilitato=True, modalita=ModalitaPonte.OMBRA)
        for verdetto in ("procedi", "evita"):
            assert ponte.blocca(self._consiglio(verdetto)) is False

    def test_in_veto_puo_solo_fermare(self, tmp_path: Path) -> None:
        ponte = PonteAI(cartella=tmp_path, abilitato=True, modalita=ModalitaPonte.VETO)
        assert ponte.blocca(self._consiglio("evita")) is True
        assert ponte.blocca(self._consiglio("procedi")) is False, (
            "un 'procedi' non e' un permesso: la decisione resta del bot"
        )

    def test_non_esiste_una_modalita_che_apre(self) -> None:
        """Se un giorno qualcuno aggiungesse 'esegui', questo test lo
        fermerebbe prima della revisione."""
        assert {m.value for m in ModalitaPonte} == {"ombra", "veto"}

    def test_il_sorgente_non_invia_ordini(self) -> None:
        """Si guarda il codice, non i commenti: le parole nei commenti
        spiegano la regola, il codice deve rispettarla."""
        import ast

        albero = ast.parse(Path("ai/ponte.py").read_text(encoding="utf-8"))
        importati = {
            nodo.module or ""
            for nodo in ast.walk(albero)
            if isinstance(nodo, ast.ImportFrom)
        } | {
            alias.name
            for nodo in ast.walk(albero)
            if isinstance(nodo, ast.Import)
            for alias in nodo.names
        }
        assert not any(
            modulo.startswith(("execution", "runner", "risk")) for modulo in importati
        ), f"il ponte non deve conoscere l'esecuzione: importa {sorted(importati)}"

        chiamate = {
            nodo.func.attr
            for nodo in ast.walk(albero)
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)
        }
        assert "submit" not in chiamate
        assert "cancel" not in chiamate


class TestCollegamentoAlBot:
    """Collegato al bot, il ponte deve restare invisibile finche' e'
    spento, e tracciabile quando e' acceso."""

    def test_spento_il_bot_non_crea_nemmeno_la_cartella(
        self, tmp_path: Path
    ) -> None:
        from config.schema import load_config
        from tests.test_runner import make_bot

        config = load_config(Path("config/config.yaml"))
        bot = make_bot(config, tmp_path)
        assert bot.ponte_ai is not None
        assert bot.ponte_ai.abilitato is False
        assert not (tmp_path / "ai").exists()

    @pytest.mark.asyncio
    async def test_acceso_in_ombra_registra_ma_non_cambia_la_decisione(
        self, tmp_path: Path
    ) -> None:
        from config.schema import load_config
        from tests.test_runner import feed_candles, make_bot

        config = load_config(Path("config/config.yaml"))
        raw = config.model_dump()
        raw["ai"] = {
            "abilitato": True,
            "modalita": "ombra",
            "cartella": str(tmp_path / "ai"),
            "scadenza_s": 300,
        }
        config = type(config).model_validate(raw)

        bot = make_bot(config, tmp_path)
        feed_candles(bot, config.features.warmup_bars + 1)
        ordine = await bot.evaluate_symbol("BTCUSDT")

        assert ordine is not None, "in ombra la decisione resta quella del bot"
        assert bot.ponte_ai is not None
        assert bot.ponte_ai.domande > 0
        richieste = list((tmp_path / "ai").glob("richiesta-*.json"))
        assert richieste, "la domanda deve restare scritta, per poterla leggere"
