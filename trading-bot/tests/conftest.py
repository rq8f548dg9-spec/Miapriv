"""Fixture condivise e isolamento dell'ambiente.

I test non devono mai ereditare l'autorizzazione al live trading
dall'ambiente in cui girano: la fixture ``autouse`` la rimuove sempre.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

import pytest
import yaml

from config.credentials import (
    LIVE_KEY_VAR,
    LIVE_SECRET_VAR,
    TESTNET_KEY_VAR,
    TESTNET_SECRET_VAR,
)
from config.live_guard import LIVE_TRADING_ENV_VAR

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "config.yaml"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Rimuove le variabili sensibili prima di ogni test."""
    for var in (
        LIVE_TRADING_ENV_VAR,
        LIVE_KEY_VAR,
        LIVE_SECRET_VAR,
        TESTNET_KEY_VAR,
        TESTNET_SECRET_VAR,
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture
def base_config_dict() -> dict[str, Any]:
    """Il config.yaml reale, come dizionario modificabile dai test.

    Usare il file vero e non un mock: cosi' un test fallisce anche quando e' la
    configurazione di produzione a diventare incoerente.
    """
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    return data


class WriteConfig(Protocol):
    """Firma della fixture ``write_config``, per annotare i test."""

    def __call__(self, data: dict[str, Any], name: str = ...) -> Path: ...


@pytest.fixture
def write_config(tmp_path: Path) -> WriteConfig:
    """Scrive un dizionario come YAML temporaneo e ne restituisce il percorso."""

    def _write(data: dict[str, Any], name: str = "config.yaml") -> Path:
        path = tmp_path / name
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    return _write
