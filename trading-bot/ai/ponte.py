"""Ponte verso l'assistente: osserva, al massimo ferma, mai apre.

Il vincolo di partenza: nel bot non deve esserci nessuna chiave di
nessun fornitore di modelli. L'assistente e' quello gia' autorizzato sul
computer, e il collegamento passa da una cartella condivisa: il bot
lascia una domanda in un file, l'assistente lascia la risposta accanto.
Nessuna chiamata di rete parte da qui, e non c'e' niente da autenticare.

Il vincolo piu' importante e' un altro, ed e' una scelta di progetto, non
una limitazione tecnica: **un consiglio non apre posizioni**. Al massimo
puo' fermarne una, e solo se qualcuno ha acceso di proposito quella
modalita'. Le ragioni sono due. La prima e' che un suggerimento che
diventa un ordine crea un sistema in cui nessuno sa piu' chi ha deciso.
La seconda e' asimmetrica e decide da sola: un veto sbagliato fa perdere
un'occasione, un'apertura sbagliata fa perdere denaro.

Per questo qui dentro non si importa il broker, non si costruiscono
ordini e non esiste una modalita' "esegui". Un test lo verifica
leggendo il sorgente, perche' le regole che contano vanno rese difficili
da violare per distrazione.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.types import TimestampMs

__all__ = ["CHIAVI_DA_NON_CONDIVIDERE", "Consiglio", "ModalitaPonte", "PonteAI"]

logger = logging.getLogger("trading_bot.ai")

#: Verdetti ammessi. Qualunque altra cosa viene scartata: un modello che
#: risponde fuori contratto non va interpretato con buona volonta'.
_VERDETTI = frozenset({"procedi", "evita", "incerto"})

#: Campi che non escono mai dal processo, nemmeno dentro un contesto.
#: La cartella e' leggibile da chiunque usi il computer.
CHIAVI_DA_NON_CONDIVIDERE = (
    "api_key",
    "api_secret",
    "secret",
    "token",
    "password",
    "chiave",
    "segreto",
)


class ModalitaPonte(StrEnum):
    """Cosa puo' fare un consiglio. Non c'e' una voce che apre."""

    #: Registrato e mostrato, non cambia nessuna decisione.
    OMBRA = "ombra"
    #: Puo' solo impedire un'apertura gia' approvata dal bot.
    VETO = "veto"


@dataclass(frozen=True, slots=True)
class Consiglio:
    """Risposta dell'assistente, gia' validata."""

    id: str
    verdetto: str
    motivo: str
    ts_ms: TimestampMs


def _ripulisci(contesto: dict[str, Any]) -> dict[str, Any]:
    """Toglie tutto cio' che somiglia a un segreto, a ogni livello."""
    pulito: dict[str, Any] = {}
    for chiave, valore in contesto.items():
        if any(proibita in chiave.lower() for proibita in CHIAVI_DA_NON_CONDIVIDERE):
            continue
        pulito[chiave] = _ripulisci(valore) if isinstance(valore, dict) else valore
    return pulito


@dataclass
class PonteAI:
    """Scambio di messaggi su file con l'assistente del computer."""

    cartella: Path
    #: Spento di default. Accenderlo e' una scelta esplicita di chi usa il
    #: bot, non qualcosa che succede installandolo.
    abilitato: bool = False
    modalita: ModalitaPonte = ModalitaPonte.OMBRA
    #: Oltre questi secondi un consiglio non vale piu': riguardava un
    #: mercato che non c'e' piu'.
    scadenza_s: int = 300
    domande: int = field(default=0, init=False)
    consigli_letti: int = field(default=0, init=False)
    veti_applicati: int = field(default=0, init=False)

    def chiedi(
        self,
        domanda: str,
        contesto: dict[str, Any],
        *,
        ts_ms: TimestampMs = 0,
    ) -> str | None:
        """Lascia una domanda. Restituisce l'identificativo, o None se spento."""
        if not self.abilitato:
            return None
        identificativo = uuid.uuid4().hex[:12]
        self.cartella.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": identificativo,
            "ts_ms": ts_ms,
            "domanda": domanda,
            "contesto": _ripulisci(contesto),
            "risposta_attesa": {
                "verdetto": sorted(_VERDETTI),
                "motivo": "una frase",
                "nota": (
                    "Il verdetto 'procedi' non autorizza niente: la decisione "
                    "resta del bot. 'evita' puo' fermare un'apertura solo se la "
                    "modalita' veto e' accesa."
                ),
            },
        }
        (self.cartella / f"richiesta-{identificativo}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.domande += 1
        return identificativo

    def leggi_risposta(
        self, identificativo: str, *, now_ms: TimestampMs = 0
    ) -> Consiglio | None:
        """Legge la risposta, se c'e', e' valida e non e' scaduta."""
        if not self.abilitato:
            return None
        file = self.cartella / f"risposta-{identificativo}.json"
        if not file.exists():
            return None
        try:
            dati = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("risposta illeggibile per %s: %s", identificativo, exc)
            return None

        verdetto = str(dati.get("verdetto", ""))
        if verdetto not in _VERDETTI:
            logger.warning(
                "verdetto fuori contratto (%r) per %s: scartato", verdetto, identificativo
            )
            return None

        ts = int(dati.get("ts_ms", 0))
        if now_ms and ts and now_ms - ts > self.scadenza_s * 1000:
            logger.info(
                "consiglio %s scaduto (%.0fs fa): riguardava un altro mercato",
                identificativo,
                (now_ms - ts) / 1000,
            )
            return None

        self.consigli_letti += 1
        return Consiglio(
            id=identificativo,
            verdetto=verdetto,
            motivo=str(dati.get("motivo", "")),
            ts_ms=ts,
        )

    def blocca(self, consiglio: Consiglio | None) -> bool:
        """Vero solo se questo consiglio deve fermare un'apertura.

        In ombra non ferma niente, per definizione. In veto ferma solo su
        "evita": un "procedi" non e' un permesso, perche' qui nessuno da'
        permessi, si tolgono soltanto.
        """
        if consiglio is None or not self.abilitato:
            return False
        if self.modalita is not ModalitaPonte.VETO:
            return False
        if consiglio.verdetto != "evita":
            return False
        self.veti_applicati += 1
        logger.warning("apertura fermata dal consiglio %s: %s", consiglio.id, consiglio.motivo)
        return True
