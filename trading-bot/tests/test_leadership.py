"""Test dell'unicita' dell'emittente: un solo processo puo' inviare ordini.

Due processi che operano sullo stesso conto non si limitano a duplicare gli
ordini: ognuno vede meta' della verita', e nessuno dei due riconcilia
correttamente. Il caso reale non e' due bot avviati per sbaglio, e' il
processo vecchio che non e' ancora morto mentre il nuovo e' gia' partito.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.clock import SimulatedClock
from execution.leadership import FileLease, LeaseLostError

NOW = 1_700_000_000_000


def lease(path: Path, owner: str, clock: SimulatedClock, ttl_ms: int = 30_000) -> FileLease:
    return FileLease(path=path, clock=clock, owner=owner, ttl_ms=ttl_ms)


class TestAcquisizione:
    def test_il_primo_prende_la_guida(self, tmp_path: Path) -> None:
        clock = SimulatedClock(NOW)
        primo = lease(tmp_path / "leader.json", "primo", clock)
        assert primo.acquire()
        assert primo.holds()
        assert primo.token == 1

    def test_il_secondo_non_la_strappa(self, tmp_path: Path) -> None:
        clock = SimulatedClock(NOW)
        percorso = tmp_path / "leader.json"
        primo = lease(percorso, "primo", clock)
        assert primo.acquire()

        secondo = lease(percorso, "secondo", clock)
        assert not secondo.acquire()
        assert not secondo.holds()
        assert primo.holds()

    def test_dopo_la_scadenza_la_guida_passa(self, tmp_path: Path) -> None:
        """Il processo vecchio e' morto senza rilasciare: il nuovo deve poter
        partire, ma solo quando il lease e' scaduto davvero."""
        clock = SimulatedClock(NOW)
        percorso = tmp_path / "leader.json"
        morto = lease(percorso, "morto", clock, ttl_ms=10_000)
        assert morto.acquire()

        clock.advance_by(9_000)
        subentrante = lease(percorso, "subentrante", clock, ttl_ms=10_000)
        assert not subentrante.acquire(), "il lease non e' ancora scaduto"

        clock.advance_by(2_000)
        assert subentrante.acquire()
        assert subentrante.token == 2, "il contrassegno deve crescere a ogni passaggio"

    def test_il_rilascio_libera_subito(self, tmp_path: Path) -> None:
        clock = SimulatedClock(NOW)
        percorso = tmp_path / "leader.json"
        primo = lease(percorso, "primo", clock)
        primo.acquire()
        primo.release()

        secondo = lease(percorso, "secondo", clock)
        assert secondo.acquire()


class TestProcessoSuperato:
    """Il cuore del requisito: dimostrare che chi e' stato superato non
    riesce piu' a operare, non che si limita a saperlo."""

    def test_il_superato_perde_la_guida_e_lo_sa(self, tmp_path: Path) -> None:
        clock = SimulatedClock(NOW)
        percorso = tmp_path / "leader.json"
        vecchio = lease(percorso, "vecchio", clock, ttl_ms=10_000)
        vecchio.acquire()

        clock.advance_by(11_000)
        nuovo = lease(percorso, "nuovo", clock, ttl_ms=10_000)
        assert nuovo.acquire()

        assert not vecchio.holds()
        assert not vecchio.renew()
        with pytest.raises(LeaseLostError):
            vecchio.require()

    def test_il_rinnovo_tiene_viva_la_guida(self, tmp_path: Path) -> None:
        clock = SimulatedClock(NOW)
        percorso = tmp_path / "leader.json"
        vivo = lease(percorso, "vivo", clock, ttl_ms=10_000)
        vivo.acquire()

        for _ in range(5):
            clock.advance_by(4_000)
            assert vivo.renew()

        subentrante = lease(percorso, "subentrante", clock, ttl_ms=10_000)
        assert not subentrante.acquire()

    def test_il_superato_non_puo_riprendersela_di_nascosto(self, tmp_path: Path) -> None:
        """Un rinnovo del superato non deve sovrascrivere il titolare."""
        clock = SimulatedClock(NOW)
        percorso = tmp_path / "leader.json"
        vecchio = lease(percorso, "vecchio", clock, ttl_ms=10_000)
        vecchio.acquire()
        clock.advance_by(11_000)
        nuovo = lease(percorso, "nuovo", clock, ttl_ms=10_000)
        nuovo.acquire()

        vecchio.renew()
        assert nuovo.holds(), "il titolare e' ancora il nuovo"
        assert not vecchio.holds()


class TestFileRovinato:
    def test_un_file_illeggibile_non_si_sovrascrive_in_silenzio(
        self, tmp_path: Path
    ) -> None:
        """Cancellare un lease che non si riesce a leggere e' il modo piu'
        rapido per ritrovarsi due processi che inviano ordini."""
        percorso = tmp_path / "leader.json"
        percorso.write_text("{non e' json", encoding="utf-8")
        clock = SimulatedClock(NOW)
        with pytest.raises(ValueError, match="illeggibile"):
            lease(percorso, "primo", clock).acquire()
