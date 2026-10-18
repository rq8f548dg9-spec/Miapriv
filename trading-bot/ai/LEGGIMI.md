# Collegare l'assistente (Claude Code) al bot

Spento di default. Nel bot **non c'è nessuna chiave** di nessun fornitore
di modelli, e nessuna chiamata di rete parte da qui: lo scambio avviene
lasciando file in una cartella, e l'assistente è quello già autorizzato
sul computer.

## Cosa può fare, e cosa no

| | ombra | veto |
|---|---|---|
| Vede le decisioni | sì | sì |
| Le registra | sì | sì |
| Può **fermare** un'apertura | no | sì |
| Può **aprire** una posizione | **no** | **no** |

Non esiste una modalità in cui l'assistente apre posizioni, e non è una
dimenticanza. Un veto sbagliato fa perdere un'occasione; un'apertura
sbagliata fa perdere denaro. L'asimmetria decide da sola.

## Come si accende

In `config/config.yaml`:

```yaml
ai:
  abilitato: true
  modalita: ombra        # comincia da qui, sempre
  cartella: state/ai
  scadenza_s: 300
```

Il bot scrive `state/ai/richiesta-<id>.json` con la decisione già presa e
il contesto (mai chiavi: i campi che somigliano a un segreto vengono tolti
prima di scrivere). Chi risponde lascia accanto `state/ai/risposta-<id>.json`:

```json
{
  "id": "<lo stesso id>",
  "verdetto": "evita",
  "motivo": "spread largo e libro sottile",
  "ts_ms": 1700000000000
}
```

Verdetti ammessi: `procedi`, `evita`, `incerto`. Qualunque altra cosa
viene scartata senza interpretazioni di buona volontà. Una risposta più
vecchia di `scadenza_s` viene ignorata: riguardava un altro mercato.

## Perché file e non una chiamata di rete

Perché così il bot non ha bisogno di credenziali di nessun servizio, e
chi guarda la cartella vede esattamente cosa è stato chiesto e cosa è
stato risposto. Un ponte che si può leggere con `cat` è un ponte che si
può controllare.
