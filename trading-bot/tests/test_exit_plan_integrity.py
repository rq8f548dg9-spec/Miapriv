"""Validazione dei piani di uscita prima che guidino ordini protettivi."""

from __future__ import annotations

from decimal import Decimal

import pytest

from risk.plan import PianoDiUscita

D = Decimal


class TestPianoDiUscitaIntegrity:
    @pytest.mark.parametrize(
        ("long", "entry", "stop", "target"),
        [
            (True, "100", "90", "120"),
            (False, "100", "110", "80"),
        ],
    )
    def test_accetta_livelli_validi_per_entrambi_i_lati(
        self, long: bool, entry: str, stop: str, target: str
    ) -> None:
        piano = PianoDiUscita("BTCUSDT", D(entry), D(stop), D(target), long, 1)

        assert PianoDiUscita.from_dict("BTCUSDT", piano.to_dict()) == piano

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("entry_price", D("0")),
            ("entry_price", D("-1")),
            ("entry_price", D("NaN")),
            ("entry_price", D("Infinity")),
            ("stop_price", D("0")),
            ("stop_price", D("-1")),
            ("stop_price", D("NaN")),
            ("stop_price", D("Infinity")),
            ("target_price", D("0")),
            ("target_price", D("-1")),
            ("target_price", D("NaN")),
            ("target_price", D("Infinity")),
        ],
    )
    def test_rifiuta_prezzi_non_finiti_o_non_positivi(self, field: str, value: Decimal) -> None:
        values = {
            "entry_price": D("100"),
            "stop_price": D("90"),
            "target_price": D("120"),
        }
        values[field] = value

        with pytest.raises(ValueError):
            PianoDiUscita("BTCUSDT", **values, long=True, created_ms=1)

    @pytest.mark.parametrize(
        ("long", "entry", "stop", "target"),
        [
            (True, "100", "100", "120"),
            (True, "100", "110", "120"),
            (True, "100", "90", "100"),
            (True, "100", "90", "80"),
            (False, "100", "110", "100"),
            (False, "100", "90", "80"),
            (False, "100", "110", "120"),
            (False, "100", "100", "80"),
        ],
    )
    def test_rifiuta_livelli_che_non_inquadrano_lentrata(
        self, long: bool, entry: str, stop: str, target: str
    ) -> None:
        with pytest.raises(ValueError):
            PianoDiUscita("BTCUSDT", D(entry), D(stop), D(target), long, 1)

    def test_rifiuta_timestamp_negativo(self) -> None:
        with pytest.raises(ValueError):
            PianoDiUscita("BTCUSDT", D("100"), D("90"), D("120"), True, -1)

    def test_from_dict_rifiuta_long_diverso_da_zero_o_uno(self) -> None:
        payload = {
            "entry_price": "100",
            "stop_price": "110",
            "target_price": "80",
            "long": "forse",
            "created_ms": "1",
        }

        with pytest.raises(ValueError, match="long"):
            PianoDiUscita.from_dict("BTCUSDT", payload)

    @pytest.mark.parametrize(
        "field,value",
        [("stop_price", "100"), ("target_price", "90"), ("entry_price", "0")],
    )
    def test_from_dict_rifiuta_livelli_semanticamente_non_validi(
        self, field: str, value: str
    ) -> None:
        payload = {
            "entry_price": "100",
            "stop_price": "90",
            "target_price": "120",
            "long": "1",
            "created_ms": "1",
        }
        payload[field] = value

        with pytest.raises(ValueError):
            PianoDiUscita.from_dict("BTCUSDT", payload)
