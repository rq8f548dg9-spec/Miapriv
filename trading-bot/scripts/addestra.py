"""Addestra il modello sui dati salvati, e lo salva solo se regge.

Il modello non viene collegato al bot da questo script. Viene addestrato,
misurato fuori campione e scritto su file; abilitarlo resta una riga di
configurazione che cambia una persona, dopo aver letto i numeri.

Tre controlli, in quest'ordine, perche' il primo che fallisce rende
inutili i successivi:

1. **c'e' qualcosa da imparare?** Se, contati i costi, quasi nessun
   campione e' positivo, non c'e' segnale da cercare: c'e' un mercato che
   su quell'orizzonte non paga le commissioni.
2. **il modello distingue?** Si misura fuori campione, su una coda di
   dati che l'addestramento non ha visto.
3. **le probabilita' sono vere?** E' il controllo che conta di piu': Kelly
   dimensiona in base al numero che il modello dichiara. Un modello che
   dice 0,70 quando la verita' e' 0,52 non sbaglia di poco, fa scommettere
   molto piu' del dovuto.

    python -m scripts.addestra --orizzonte 5 --uscita state/modello.json
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from apprendimento.dataset import (
    Campione,
    costruisci_dataset,
    feature_utilizzabili,
    quota_positivi,
)
from config.schema import load_config
from data.store import ParquetStore
from monitoring.logger import setup_logging
from signals.logistic import addestra
from signals.ml import expected_calibration_error

logger = logging.getLogger("trading_bot.addestra")

#: Sotto questa quota di campioni positivi non c'e' niente da imparare:
#: il modello imparerebbe solo a dire sempre no.
MIN_QUOTA_POSITIVI = 0.05
#: Soglia di calibrazione oltre la quale il modello non e' utilizzabile
#: dentro Kelly. E' la stessa che il generatore applica a run time.
MAX_ERRORE_CALIBRAZIONE = 0.05


def _auc(previste: list[float], osservate: list[bool]) -> float:
    """Area sotto la curva ROC, calcolata per ranghi.

    Risponde a una domanda diversa dalla calibrazione: non "la
    probabilita' e' giusta?" ma "il modello mette i casi buoni davanti ai
    cattivi?". Un modello puo' essere calibrato e inutile, e senza questo
    numero non si vede.
    """
    positivi = [p for p, o in zip(previste, osservate, strict=True) if o]
    negativi = [p for p, o in zip(previste, osservate, strict=True) if not o]
    if not positivi or not negativi:
        return 0.5
    coppie = sorted(
        [(p, 1) for p in positivi] + [(p, 0) for p in negativi], key=lambda x: x[0]
    )
    somma_ranghi = 0.0
    i = 0
    while i < len(coppie):
        j = i
        while j + 1 < len(coppie) and coppie[j + 1][0] == coppie[i][0]:
            j += 1
        rango_medio = (i + j) / 2 + 1
        for k in range(i, j + 1):
            if coppie[k][1] == 1:
                somma_ranghi += rango_medio
        i = j + 1
    n_pos, n_neg = len(positivi), len(negativi)
    return (somma_ranghi - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def _separa(
    campioni: list[Campione], quota_prova: float
) -> tuple[list[Campione], list[Campione]]:
    """Taglio cronologico: il passato addestra, il futuro verifica.

    Mescolare a caso darebbe un modello che ha visto il futuro dei
    campioni su cui viene misurato, e un risultato che non si ripresenta
    mai dal vivo.
    """
    ordinati = sorted(campioni, key=lambda c: c.ts_ms)
    taglio = int(len(ordinati) * (1.0 - quota_prova))
    return ordinati[:taglio], ordinati[taglio:]


def esegui(
    config_path: Path,
    *,
    orizzonte: int,
    uscita: Path,
    quota_prova: float = 0.3,
    passi: int = 800,
) -> int:
    config = load_config(config_path)
    store = ParquetStore(config.data.parquet_root)
    simboli = [s.symbol for s in config.enabled_symbols]

    candele = store.read_candles(symbols=simboli)
    if not candele:
        logger.error(
            "nessuna candela in %s: scarica prima lo storico", config.data.parquet_root
        )
        return 2
    flusso = store.read_trades(
        symbols=simboli,
        start_ms=candele[0].open_time_ms,
        end_ms=candele[-1].close_time_ms + 1,
    )
    if not flusso:
        logger.warning(
            "nessun flusso ordini salvato: il modello imparera' senza order flow, "
            "che dal vivo pesa quanto lo slancio"
        )

    campioni = costruisci_dataset(
        config, candele, flusso, orizzonte_barre=orizzonte
    )
    if len(campioni) < 500:
        logger.error(
            "solo %d campioni utilizzabili: troppo pochi per addestrare qualunque "
            "cosa. Scarica piu' storico.",
            len(campioni),
        )
        return 2

    quota = quota_positivi(campioni)
    logger.info(
        "%d campioni, %.1f%% positivi (rialzi che pagano i costi su %d barre)",
        len(campioni),
        100 * quota,
        orizzonte,
    )
    if quota < MIN_QUOTA_POSITIVI:
        logger.error(
            "solo il %.1f%% dei campioni paga i costi su questo orizzonte: non c'e' "
            "un segnale da imparare, c'e' un orizzonte troppo corto per le "
            "commissioni. Prova --orizzonte piu' lungo.",
            100 * quota,
        )
        return 1

    feature = feature_utilizzabili(campioni)
    logger.info("feature usate (%d): %s", len(feature), ", ".join(feature))

    addestramento, prova = _separa(campioni, quota_prova)
    logger.info(
        "addestramento su %d campioni, verifica su %d (taglio cronologico)",
        len(addestramento),
        len(prova),
    )

    modello = addestra(addestramento, feature=feature, passi=passi)

    previste = [
        modello.predict_proba([c.valori[n] for n in feature])
        for c in prova
        if all(n in c.valori for n in feature)
    ]
    osservate = [
        bool(c.etichetta) for c in prova if all(n in c.valori for n in feature)
    ]
    if len(previste) < 100:
        logger.error("troppe poche previsioni fuori campione per misurare qualcosa")
        return 1

    errore = expected_calibration_error(previste, osservate, bins=10)
    discriminazione = _auc(previste, osservate)
    media_prevista = sum(previste) / len(previste)
    media_osservata = sum(1 for o in osservate if o) / len(osservate)
    logger.info(
        "fuori campione: probabilita' media prevista %.3f, frequenza reale %.3f, "
        "errore di calibrazione %.4f, capacita' di distinguere (AUC) %.3f",
        media_prevista,
        media_osservata,
        errore,
        discriminazione,
    )
    if discriminazione < 0.55:
        # Un modello puo' essere perfettamente calibrato e completamente
        # inutile: se dice sempre "11%" e l'11% e' vero, ha ragione e non
        # distingue niente. Va detto, perche' il solo errore di
        # calibrazione lo promuoverebbe.
        logger.warning(
            "AUC %.3f: il modello e' calibrato ma NON distingue i casi buoni dai "
            "cattivi (0,5 = moneta). Su questi dati non c'e' segnale da imparare; "
            "il modello si salva lo stesso, ma non aspettarti che operi.",
            discriminazione,
        )

    if errore > MAX_ERRORE_CALIBRAZIONE:
        logger.error(
            "errore di calibrazione %.4f oltre il massimo %.4f: il modello NON viene "
            "salvato. Le probabilita' sbagliate non sono un errore piccolo: Kelly ci "
            "dimensiona sopra.",
            errore,
            MAX_ERRORE_CALIBRAZIONE,
        )
        return 1

    modello.metadati = {
        "auc_fuori_campione": f"{discriminazione:.4f}",
        "orizzonte_barre": str(orizzonte),
        "campioni_totali": str(len(campioni)),
        "quota_positivi": f"{quota:.4f}",
        "primo_campione_ms": str(min(c.ts_ms for c in campioni)),
        "ultimo_campione_ms": str(max(c.ts_ms for c in campioni)),
    }
    modello.salva(uscita, errore_calibrazione=errore, campioni=len(addestramento))
    logger.info("modello salvato in %s", uscita)
    print(
        f"Modello salvato in {uscita}.\n"
        f"Errore di calibrazione fuori campione: {errore:.4f} "
        f"(massimo ammesso {MAX_ERRORE_CALIBRAZIONE}).\n"
        "NON e' ancora collegato al bot: per usarlo, in configurazione metti\n"
        "  signals.generator: ml\n"
        f"  signals.ml_enabled: true\n  signals.ml_model_path: {uscita}\n"
        "Prima pero' conviene rifare la prova della strategia con il modello "
        "acceso, e guardare se regge su finestre diverse."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Addestra il modello sui dati salvati")
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument(
        "--orizzonte",
        type=int,
        default=5,
        help="su quante barre si misura se l'operazione avrebbe pagato",
    )
    parser.add_argument("--uscita", type=Path, default=Path("state/modello.json"))
    parser.add_argument("--passi", type=int, default=800)
    args = parser.parse_args()

    setup_logging(level="INFO", json_logs=False)
    return esegui(
        args.config, orizzonte=args.orizzonte, uscita=args.uscita, passi=args.passi
    )


if __name__ == "__main__":
    raise SystemExit(main())
