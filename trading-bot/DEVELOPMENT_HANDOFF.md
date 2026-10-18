# Incremento integrità — 17 ottobre 2026

Base: `0b00b0bc4af21fa0f13c69f27b2c9b46118bbdb5`. Branch locale: `codex/miapriv-95-foundation`.

## Modifiche verificate

- Registro ordini: schema e quantità finite/valide; rifiuto di aggiornamenti con quantità cumulativa decrescente o riapertura di ordini terminali. Un ordine cancellato parzialmente può ancora diventare eseguito con nuovi fill. Una validazione fallita non modifica i record già validi. Questo NON è ancora un ledger transazionale o un lock multiprocesso.
- Ripristino: il runner rifiuta snapshot esistenti illeggibili/incompatibili e piani di uscita invalidi. I saldi exchange non bastano a ricostruire le protezioni perse. I lettori diagnostici mantengono `StateStore.load()` tollerante; il runner usa `strict=True`.
- Rischio giornaliero: persiste `extra.risk_day_key` UTC, conserva il riferimento dopo riavvio nello stesso giorno e passa al nuovo giorno prima del controllo giornaliero. Il massimo storico e gli halt restano separati e non vengono azzerati dal cambio giorno. Compatibilità v4: se la chiave non esiste, usa il giorno UTC di `saved_at_ms`; non è possibile ricostruire un riferimento storico non salvato.
- Stato: flag/timestamp halt coerenti, timestamp validi, riferimenti di equity finiti e non negativi, giorno canonico non futuro rispetto al salvataggio.
- Piani: prezzi finiti e positivi, stop/target ai lati corretti dell'entrata, direzione serializzata esplicita e timestamp non negativo.
- Test POSIX dei permessi limitato alla piattaforma pertinente. Test Windows adiacenti conservati; lo skip non dimostra le ACL reali.

## Verifica

Python 3.13.15 su Windows; ambiente preesistente dell'audit. Eseguiti dalla directory `trading-bot`:

```text
python -m pytest       -> 621 passed, 1 skipped, 27 warnings (9,78 s)
python -m ruff check . -> All checks passed
python -m mypy .       -> no issues in 70 source files
python -m pip wheel . --no-deps --no-build-isolation --no-index --wheel-dir <evidence>/dist
                      -> BLOCCATO: setuptools.build_meta assente
```

Baseline: 558 pass e 1 fallimento POSIX su Windows. Nuovi test scritti ed eseguiti in RED prima delle modifiche. Avvisi preesistenti `aiohttp.NotAppKeyWarning` nel pannello. Revisione indipendente su gpt-6-astra; due rilievi su halt e risk_day_key corretti con 8 regressioni RED→GREEN e successiva suite completa. Modelli gpt-6-luna e gpt-6-sol impiegati per compiti circoscritti. Risparmio token non misurato.

## Ripresa e recupero

Non cancellare snapshot o registro per aggirare un errore di avvio. Conservare una copia dei file senza segreti, identificare l'incoerenza e ripristinare un backup verificato. Un ripristino errato può perdere halt, massimo storico, proprietà delle posizioni e piani. Validare offline prima di riprendere; nessun trading autorizzato in questo incremento.

Per annullare questo incremento usare il commit dedicato con un revert su branch di lavoro dopo aver verificato eventuali modifiche successive. Non usare reset distruttivi sulla cartella di Claude. Il rollback del codice non ripristina lo stato finanziario e la procedura operativa non è stata collaudata su un bot live.

## Prossima attività: fondazioni transazionali

1. Leggere checkpoint completo e prompt 95 nella cartella outputs della chat, verificare branch/commit e le modifiche remote eventualmente successive. Nessun push eseguito.
2. Progettare ledger SQLite transazionale e singolo emittente prima di aggiungere ML: persistenza di intenzioni/fill/protezioni, deduplica durevole, prenotazioni atomiche, esiti sconosciuti e riconciliazione. Test di crash nei punti di invio/persistenza e due processi concorrenti.
3. Risolvere deduplica fill attuale che espelle vecchie chiavi dopo 20.000 eventi e collisioni ID di decisioni diverse nello stesso secondo. Non sostituire semplicemente l'ID con UUID casuali ai retry.
4. Verificare contabilità delle fee in terzo asset, flussi esterni, reinvestimento dopo restart e continuità delle chiusure forzate dopo crash. In particolare `KillSwitch.restore` attualmente ripristina `_flatten_pending=False`: non è una prova che l'inventario sia stato chiuso.
5. Dati/replay, baseline validata fuori campione, modelli e scenari, apprendimento controllato e MCP shadow; seguire i criteri completi del prompt.
6. Mancano prove prospettiche economiche, soak reale 72h, round-trip client, matrice A–J completa, benchmark e collaudo di backup/restore.

Il risultato è un incremento verificato, NON il completamento del prompt. Non è stato attribuito il punteggio 95/100 né dimostrata redditività. Non eseguire ordini o inviare dati/chiavi ai modelli per proseguire lo sviluppo offline.
