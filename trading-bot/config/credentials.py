"""Credenziali API: lette dall'ambiente, mai dal codice, mai nei log.

Le chiavi testnet e live stanno in variabili con nomi diversi, cosi' una
configurazione sbagliata produce un errore di avvio e non un ordine reale
inviato per sbaglio con le chiavi giuste.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr

from config.live_guard import is_live_trading_authorized
from config.schema import Mode

__all__ = ["Credentials", "CredentialsError", "load_credentials", "load_dotenv"]

TESTNET_KEY_VAR = "BINANCE_TESTNET_API_KEY"
TESTNET_SECRET_VAR = "BINANCE_TESTNET_API_SECRET"  # noqa: S105 - nome di variabile, non segreto
LIVE_KEY_VAR = "BINANCE_LIVE_API_KEY"
LIVE_SECRET_VAR = "BINANCE_LIVE_API_SECRET"  # noqa: S105 - nome di variabile, non segreto


class CredentialsError(RuntimeError):
    """Credenziali mancanti o incoerenti con la modalita' richiesta."""


class Credentials(BaseModel):
    """Chiavi API. ``SecretStr`` impedisce che finiscano in un repr o in un log."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: SecretStr
    api_secret: SecretStr
    mode: Mode

    def __str__(self) -> str:
        return f"Credentials(mode={self.mode.value}, api_key=[REDACTED], api_secret=[REDACTED])"


def load_dotenv(path: str | Path = ".env", *, override: bool = False) -> int:
    """Carica un file ``.env`` nell'ambiente. Restituisce quante variabili ha scritto.

    Parser volutamente minimale (``KEY=value``, ``#`` per i commenti): evita una
    dipendenza esterna per venti righe di codice. Un file assente non e' un
    errore: in produzione le variabili arrivano dall'orchestratore.
    """
    env_path = Path(path)
    if not env_path.is_file():
        return 0

    written = 0
    # split("\n") e non splitlines(): quest'ultimo spezza anche su \x85,
    # \u2028 e altri caratteri, trasformando un valore che li contiene in
    # righe aggiuntive. Un file .env non ha mai righe separate da quei
    # caratteri, e trattarli come testo normale toglie la via d'attacco.
    for raw_line in env_path.read_text(encoding="utf-8").split("\n"):
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
            written += 1
    return written


def load_credentials(mode: Mode) -> Credentials | None:
    """Credenziali per la modalita' indicata.

    ``backtest`` non ne usa: restituisce ``None`` invece di chiedere chiavi che
    non servono. Le chiavi live si leggono solo se la guardia e' soddisfatta.
    """
    if mode is Mode.BACKTEST:
        return None

    if mode is Mode.LIVE:
        if not is_live_trading_authorized():
            raise CredentialsError(
                "richieste credenziali live senza autorizzazione esplicita: "
                "la guardia di config.live_guard non e' stata soddisfatta"
            )
        key_var, secret_var = LIVE_KEY_VAR, LIVE_SECRET_VAR
    else:
        key_var, secret_var = TESTNET_KEY_VAR, TESTNET_SECRET_VAR

    key = os.environ.get(key_var, "")
    secret = os.environ.get(secret_var, "")
    missing = [name for name, value in ((key_var, key), (secret_var, secret)) if not value]
    if missing:
        raise CredentialsError(
            f"variabili d'ambiente mancanti per mode={mode.value}: {', '.join(missing)}. "
            "Impostale in .env (mai nel codice)."
        )

    return Credentials(api_key=SecretStr(key), api_secret=SecretStr(secret), mode=mode)
