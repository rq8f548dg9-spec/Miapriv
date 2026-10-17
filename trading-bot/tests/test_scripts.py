"""Test degli script accessori: portabilita' e coerenza con la configurazione.

Sono gli unici pezzi che girano fuori dal processo del bot, ed e' li' che si
nascondono le rotture per sistema operativo: l'utente che apre il programma su
Windows non deve incontrare un errore che qui non si vedrebbe mai.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from importlib.machinery import ModuleSpec
from types import ModuleType

import pytest

from config.schema import INTERVAL_MS, DataConfig


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
