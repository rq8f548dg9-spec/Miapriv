"""Configurazione letta dalle variabili d'ambiente."""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("scarico.config")


class ConfigError(RuntimeError):
    """Configurazione mancante o non valida: il server non deve partire."""


def _int_env(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} deve essere un numero intero, trovato {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    password: str
    secret_key: str
    secret_is_ephemeral: bool
    cookies_file: Path | None
    session_days: int
    max_filesize_mb: int
    max_concurrent: int
    tmp_dir: Path

    @property
    def max_filesize_bytes(self) -> int:
        """0 significa nessun limite."""
        return max(self.max_filesize_mb, 0) * 1024 * 1024


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)

    password = env.get("APP_PASSWORD", "").strip()
    if not password:
        raise ConfigError(
            "APP_PASSWORD non e' impostata. Il sito resterebbe aperto a chiunque "
            "conosca l'indirizzo, quindi mi fermo qui. Copia .env.example in .env "
            "e scegli una password."
        )
    if len(password) < 8:
        raise ConfigError("APP_PASSWORD deve essere lunga almeno 8 caratteri.")

    secret_key = env.get("SECRET_KEY", "").strip()
    ephemeral = not secret_key
    if ephemeral:
        secret_key = secrets.token_urlsafe(48)
        log.warning(
            "SECRET_KEY non impostata: ne genero una temporanea. Ad ogni riavvio "
            "dovrai rifare il login sul telefono e rigenerare il token delle Scorciatoie."
        )

    cookies_raw = env.get("COOKIES_FILE", "").strip()
    cookies_file: Path | None = None
    if cookies_raw:
        candidate = Path(cookies_raw).expanduser()
        if candidate.is_file():
            cookies_file = candidate
        else:
            log.warning("COOKIES_FILE punta a %s che non esiste: lo ignoro.", candidate)

    tmp_raw = env.get("TMP_DIR", "").strip()
    tmp_dir = Path(tmp_raw).expanduser() if tmp_raw else Path(__file__).resolve().parent.parent / "tmp-downloads"

    return Settings(
        password=password,
        secret_key=secret_key,
        secret_is_ephemeral=ephemeral,
        cookies_file=cookies_file,
        session_days=_int_env(env, "SESSION_DAYS", 60),
        max_filesize_mb=_int_env(env, "MAX_FILESIZE_MB", 2048),
        max_concurrent=max(1, _int_env(env, "MAX_CONCURRENT", 2)),
        tmp_dir=tmp_dir,
    )
