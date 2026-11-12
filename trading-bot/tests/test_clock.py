"""Test dell'astrazione tempo.

Il backtest deve eseguire lo stesso codice del live: qui si verifica che le due
implementazioni di ``Clock`` siano intercambiabili e che il tempo simulato sia
deterministico e monotono.
"""

from __future__ import annotations

import asyncio

import pytest

from core.clock import Clock, LiveClock, SimulatedClock

START = 1_700_000_000_000


class TestProtocol:
    def test_entrambe_le_implementazioni_soddisfano_il_protocollo(self) -> None:
        assert isinstance(LiveClock(), Clock)
        assert isinstance(SimulatedClock(), Clock)

    def test_now_e_coerente_con_now_ms(self) -> None:
        clock = SimulatedClock(START)
        assert int(clock.now().timestamp() * 1000) == clock.now_ms()

    def test_now_e_sempre_utc_aware(self) -> None:
        for clock in (LiveClock(), SimulatedClock(START)):
            assert clock.now().tzinfo is not None


class TestSimulatedClock:
    def test_il_tempo_non_avanza_da_solo(self) -> None:
        clock = SimulatedClock(START)
        assert clock.now_ms() == START
        assert clock.now_ms() == START

    def test_avanzamento_esplicito(self) -> None:
        clock = SimulatedClock(START)
        clock.advance_by(1_500)
        assert clock.now_ms() == START + 1_500
        clock.advance_to(START + 2_000)
        assert clock.now_ms() == START + 2_000

    def test_tempo_allindietro_rifiutato(self) -> None:
        """Nel backtest il tempo che torna indietro segnala quasi sempre un look-ahead."""
        clock = SimulatedClock(START)
        clock.advance_by(1_000)
        with pytest.raises(ValueError, match="all'indietro"):
            clock.advance_to(START)

    def test_avanzamento_negativo_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="negativo"):
            SimulatedClock(START).advance_by(-1)

    def test_monotonic_segue_il_tempo_simulato(self) -> None:
        clock = SimulatedClock(START)
        before = clock.monotonic_ns()
        clock.advance_by(250)
        assert clock.monotonic_ns() - before == 250 * 1_000_000

    def test_stesso_seme_stessa_sequenza(self) -> None:
        """Determinismo: due run identici producono gli stessi istanti."""
        def run() -> list[int]:
            clock = SimulatedClock(START)
            stamps = []
            for step in (100, 250, 650):
                clock.advance_by(step)
                stamps.append(clock.now_ms())
            return stamps

        assert run() == run()


class TestSimulatedSleep:
    @pytest.mark.asyncio
    async def test_sleep_si_risveglia_alla_scadenza(self) -> None:
        clock = SimulatedClock(START)

        async def waiter() -> int:
            await clock.sleep(2.0)
            return clock.now_ms()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert clock.pending_wakeups == 1
        assert clock.next_wakeup_ms() == START + 2_000

        clock.advance_to(START + 2_000)
        assert await task == START + 2_000
        assert clock.pending_wakeups == 0

    @pytest.mark.asyncio
    async def test_sleep_non_si_risveglia_prima(self) -> None:
        clock = SimulatedClock(START)
        task = asyncio.create_task(clock.sleep(5.0))
        await asyncio.sleep(0)

        clock.advance_by(4_999)
        await asyncio.sleep(0)
        assert not task.done()

        clock.advance_by(1)
        await task
        assert task.done()

    @pytest.mark.asyncio
    async def test_risveglio_in_ordine_di_scadenza(self) -> None:
        clock = SimulatedClock(START)
        woken: list[str] = []

        async def waiter(name: str, seconds: float) -> None:
            await clock.sleep(seconds)
            woken.append(name)

        tasks = [
            asyncio.create_task(waiter("tardi", 3.0)),
            asyncio.create_task(waiter("presto", 1.0)),
            asyncio.create_task(waiter("medio", 2.0)),
        ]
        await asyncio.sleep(0)
        clock.advance_by(3_000)
        await asyncio.gather(*tasks)
        assert woken == ["presto", "medio", "tardi"]

    @pytest.mark.asyncio
    async def test_sleep_zero_cede_soltanto_il_controllo(self) -> None:
        clock = SimulatedClock(START)
        await clock.sleep(0)
        assert clock.now_ms() == START

    @pytest.mark.asyncio
    async def test_sleep_negativo_rifiutato(self) -> None:
        with pytest.raises(ValueError, match="negativo"):
            await SimulatedClock(START).sleep(-1.0)


class TestLiveClock:
    @pytest.mark.asyncio
    async def test_sleep_reale_avanza_lorologio(self) -> None:
        clock = LiveClock()
        before = clock.monotonic_ns()
        await clock.sleep(0.01)
        assert clock.monotonic_ns() > before

    def test_monotonic_non_decresce(self) -> None:
        clock = LiveClock()
        assert clock.monotonic_ns() <= clock.monotonic_ns()
