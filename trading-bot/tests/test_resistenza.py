"""Prova di resistenza: il bot sotto guasti, per molte ore simulate.

I difetti che contano in un bot che gira sempre non si vedono in un test
da trenta secondi: si vedono quando il flusso cade e torna, quando un
ordine resta in sospeso, quando il processo muore e riparte, quando il
computer va in standby e l'orologio salta avanti di ore. Nessuna di
queste cose e' straordinaria: su una settimana capitano tutte.

Qui si simulano, in sequenza e a tempo accelerato, e dopo ognuna si
verificano gli stessi invarianti:

* la contabilita' non raddoppia un'esecuzione;
* il patrimonio ricostruito dal registro combacia con quello in memoria;
* una chiusura d'emergenza rimasta a meta' resta in coda;
* il bot non si pianta.

Questa prova NON sostituisce le 72 ore vere: il tempo simulato non
scopre perdite di memoria, scadenze di certificati o limiti dell'API. Le
72 ore restano da fare.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from config.schema import AppConfig, load_config
from resistenza.prova import Guasto, ProvaDiResistenza

D = Decimal


@pytest.fixture
def config() -> AppConfig:
    return load_config(Path("config/config.yaml"))


class TestProvaBreve:
    @pytest.mark.asyncio
    async def test_sopravvive_a_una_sequenza_di_guasti(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        prova = ProvaDiResistenza(config=config, cartella=tmp_path, seed=7)
        esito = await prova.esegui(minuti=1_000, ogni_quanti_guasti=120)

        assert esito.cicli > 0
        assert esito.guasti_iniettati > 0
        assert esito.decisioni > 0, (
            "una prova in cui il bot non decide mai non prova il bot"
        )
        assert esito.invarianti_violati == [], esito.invarianti_violati
        assert esito.errori_non_gestiti == 0

    @pytest.mark.asyncio
    async def test_ogni_tipo_di_guasto_viene_esercitato(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        """Una prova che inietta sempre lo stesso guasto verifica un caso
        solo e sembra verificarli tutti."""
        prova = ProvaDiResistenza(config=config, cartella=tmp_path, seed=3)
        esito = await prova.esegui(minuti=2_500, ogni_quanti_guasti=90)
        assert set(esito.guasti_per_tipo) == {g.value for g in Guasto}

    @pytest.mark.asyncio
    async def test_la_contabilita_resta_coerente_dopo_i_riavvii(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        prova = ProvaDiResistenza(config=config, cartella=tmp_path, seed=11)
        esito = await prova.esegui(minuti=2_000, ogni_quanti_guasti=90)
        assert esito.riavvii > 0
        assert esito.esecuzioni > 0, (
            "senza esecuzioni la prova non tocca la contabilita', che e' "
            "esattamente la parte che i riavvii mettono a rischio"
        )
        assert esito.esecuzioni_doppie == 0

    @pytest.mark.asyncio
    async def test_il_rapporto_dichiara_cosa_non_ha_provato(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        """Un rapporto che tace sui propri limiti viene letto come una
        garanzia che non e'."""
        prova = ProvaDiResistenza(config=config, cartella=tmp_path, seed=5)
        esito = await prova.esegui(minuti=300)
        testo = esito.rapporto()
        assert "72 ore" in testo
        assert "simulat" in testo.lower()


class TestInvarianti:
    @pytest.mark.asyncio
    async def test_un_invariante_rotto_viene_segnalato(
        self, config: AppConfig, tmp_path: Path
    ) -> None:
        """La prova deve poter fallire: se non sa accorgersi di una
        contabilita' sbagliata, il suo verde non vale niente."""
        prova = ProvaDiResistenza(config=config, cartella=tmp_path, seed=2)
        await prova.esegui(minuti=120)
        assert prova.bot is not None

        # Si sporca il portafoglio senza passare dal registro: e'
        # esattamente la divergenza che gli invarianti devono vedere.
        prova.bot.portfolio.cash += D("50")
        violazioni = prova.verifica_invarianti()
        assert any("registro" in v for v in violazioni)
