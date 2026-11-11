"""Configurazione letta dalle variabili d'ambiente.

Un solo segreto da impostare: ACCESS_KEY, la chiave che sta dentro il link.
Tutto il resto ha un valore sensato di suo, perche' questo sito deve poter
essere acceso da un telefono in tre tocchi.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import tempfile
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


def _bool_env(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "si", "sì", "yes", "on")


@dataclass(frozen=True)
class Settings:
    access_key: str
    secret_key: str
    cookies_file: Path | None
    max_filesize_mb: int
    max_concurrent: int
    tmp_dir: Path
    open_mode: bool

    @property
    def max_filesize_bytes(self) -> int:
        """0 significa nessun limite."""
        return max(self.max_filesize_mb, 0) * 1024 * 1024


def _derive(access_key: str, scopo: str) -> str:
    """Chiave di firma ricavata da quella d'accesso.

    Cosi' c'e' un segreto solo da incollare, ma la chiave che firma i biglietti
    non e' la stessa che gira dentro il link.
    """
    return hmac.new(access_key.encode("utf-8"), scopo.encode("utf-8"), hashlib.sha256).hexdigest()


def _cookies_da_variabile(contenuto: str) -> Path | None:
    """Su Hugging Face i cookie arrivano come testo in un secret, non come file."""
    testo = contenuto.strip()
    if not testo:
        return None
    if "\\n" in testo and "\n" not in testo:
        # Alcuni pannelli salvano gli a capo come due caratteri: li rimetto a posto.
        testo = testo.replace("\\n", "\n")
    percorso = Path(tempfile.gettempdir()) / "ig-cookies.txt"
    percorso.write_text(testo if testo.endswith("\n") else testo + "\n", encoding="utf-8")
    percorso.chmod(0o600)
    log.info("cookie Instagram presi dalla variabile IG_COOKIES")
    return percorso


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)

    access_key = env.get("ACCESS_KEY", "").strip()
    open_mode = _bool_env(env, "OPEN_MODE", False)

    if not access_key and not open_mode:
        raise ConfigError(
            "Manca ACCESS_KEY.\n"
            "E' la chiave segreta che sta dentro il tuo link: senza, il sito sarebbe\n"
            "aperto a chiunque ne indovini l'indirizzo, quindi mi fermo qui.\n"
            "Generane una con:  python3 -c \"import secrets; print(secrets.token_urlsafe(24))\"\n"
            "e mettila tra i secret dello Space (Settings > Variables and secrets).\n"
            "Se vuoi davvero un sito aperto a tutti, imposta OPEN_MODE=1."
        )
    if access_key and len(access_key) < 16:
        raise ConfigError("ACCESS_KEY deve essere lunga almeno 16 caratteri: e' l'unica cosa che protegge il sito.")

    secret_key = env.get("SECRET_KEY", "").strip() or _derive(access_key or "aperto", "firma-biglietti")

    cookies_file = _cookies_da_variabile(env.get("IG_COOKIES", ""))
    if cookies_file is None:
        grezzo = env.get("COOKIES_FILE", "").strip()
        if grezzo:
            candidato = Path(grezzo).expanduser()
            if candidato.is_file():
                cookies_file = candidato
            else:
                log.warning("COOKIES_FILE punta a %s che non esiste: lo ignoro.", candidato)

    tmp_raw = env.get("TMP_DIR", "").strip()
    tmp_dir = Path(tmp_raw).expanduser() if tmp_raw else Path(tempfile.gettempdir()) / "scarico-pin-insta"

    return Settings(
        access_key=access_key,
        secret_key=secret_key,
        cookies_file=cookies_file,
        max_filesize_mb=_int_env(env, "MAX_FILESIZE_MB", 2048),
        max_concurrent=max(1, _int_env(env, "MAX_CONCURRENT", 2)),
        tmp_dir=tmp_dir,
        open_mode=open_mode,
    )
