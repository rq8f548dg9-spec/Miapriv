# Scarico

Sito privato per iPhone: incolli il link di un pin o di un reel, tocchi un
bottone, il video o la foto finisce **nella tua galleria**. Nessuna password da
digitare: si apre in Safari, si aggiunge alla schermata Home, e da lì si
comporta come un'app.

---

## Due cose da sapere prima

**Serve qualcosa che giri su un server.** Il browser dell'iPhone non è
autorizzato a leggere le pagine di Pinterest e Instagram per conto tuo, e il
link vero del video non è scritto nella pagina. Un sito di sole pagine
(GitHub Pages, Lovable, Netlify) si aprirebbe benissimo e non scaricherebbe
niente.

**L'ultimo tocco è tuo.** Su iOS nessuna pagina web può scrivere dentro Foto da
sola. L'unica via è il foglio di condivisione: il sito scarica il file, apre il
foglio, e tu tocchi **Salva video**. Funziona così anche nei siti che fanno
questo mestiere.

---

## Due versioni dello stesso sito

Lo stesso sito, lo stesso aspetto, due motori diversi. Scegli in base a dove lo
metti.

| | **Supabase** (`deno/`, `supabase/`) | **Python** (`server/`, `hf-space/`) |
|---|---|---|
| Dove gira | Edge Functions, piano gratuito | un computer tuo, Hugging Face PRO, Render |
| Costo | gratis | gratis in casa, a pagamento in cloud |
| Si addormenta | no | sì su Render, no in casa |
| Pinterest | video e foto | video e foto |
| Instagram | incerto: senza cookie quasi sempre no | buono, con yt-dlp |
| Storie con audio separato | **no** (manca ffmpeg) | sì |

La versione Supabase è quella consigliata se vuoi solo che funzioni, gratis e
senza pensieri. La versione Python è quella che scarica di più.

---

## A. Pubblicare su Supabase

Serve un account gratuito su [supabase.com](https://supabase.com) e un progetto
(va bene anche uno che hai già).

1. **Token**: supabase.com → in alto a destra il tuo avatar → *Account settings*
   → *Access Tokens* → **Generate new token**.
2. **Pubblica la funzione**. Dal computer, con la CLI di Supabase:
   ```bash
   npx supabase login
   npx supabase functions deploy scarico --project-ref IL-TUO-REF --no-verify-jwt
   ```
   `--no-verify-jwt` è obbligatorio: senza, Safari non potrebbe aprire il sito.
3. **La chiave**: Dashboard → *Edge Functions* → *Secrets* → aggiungi
   `ACCESS_KEY` con una frase lunga e tua (almeno 16 caratteri, senza spazi).
   Facoltativo: `IG_COOKIES` (vedi più sotto) e `MAX_FILESIZE_MB`.
4. **Il tuo link**:
   ```
   https://IL-TUO-REF.supabase.co/functions/v1/scarico/?k=LA-TUA-CHIAVE
   ```

Il file che viene pubblicato è `supabase/functions/scarico/index.ts`, ed è
**generato**: si modifica `deno/scarico.ts` e `web/`, poi si rigenera con
`python3 tools/build_supabase.py`.

## B. Pubblicare la versione Python

- **Su un computer tuo** (la migliore per Instagram): `./avvia.sh`, poi
  `tailscale serve --bg 8080` per raggiungerlo da fuori casa.
- **Con Docker**: `docker compose up -d --build`.
- **Su Hugging Face**: i quattro file pronti sono in `hf-space/`. Attenzione:
  dal 2026 gli Space Docker richiedono un abbonamento PRO, quelli gratuiti
  sono solo statici e non possono eseguire questo sito.

In tutti i casi la chiave si imposta nella variabile `ACCESS_KEY`.

---

## Metterlo sulla schermata Home

Apri il tuo link **in Safari**, poi **Condividi** → **Aggiungi a Home**.

L'icona si ricorda la chiave per sempre: da quel momento tocchi l'icona e sei
dentro, senza digitare niente. Se apri l'indirizzo senza la parte `?k=…` il
sito ti dice che manca la chiave — è voluto, è quello che tiene fuori gli altri.

## Usarlo

1. Su Pinterest o Instagram: **Condividi → Copia link**
2. Apri **Scarico**, tocca **Incolla e trova**
3. **Salva in galleria** → nel foglio che si apre, **Salva video**

C'è anche **Salva nei File** se preferisci il file alla galleria.

---

## Instagram: quando serve il login

Pinterest funziona quasi sempre senza niente. Instagram no: da un server in
cloud mostra poco agli sconosciuti. Se vedi *«Instagram chiede di essere
loggato»*, servono i tuoi cookie:

1. Da un computer, con un'estensione che esporta i cookie in formato Netscape,
   esporta quelli di `instagram.com` mentre sei loggato.
2. Metti il contenuto nel segreto `IG_COOKIES` (su Supabase: Edge Functions →
   Secrets; sulla versione Python: nel file `.env`).

Tienilo come tieni una password, perché di fatto lo è.

---

## Se qualcosa non va

| Cosa vedi | Cosa è successo |
|---|---|
| «Serve il tuo link personale» | Hai aperto l'indirizzo senza `?k=…`. Usa l'icona sulla Home. |
| «Instagram chiede di essere loggato» | Vedi sopra. |
| «In questo pin non ho trovato niente» | Il pin è stato rimosso, o è di un tipo che la pagina non espone. |
| «Questo link non è di Pinterest o Instagram» | Serve l'indirizzo del post, non altro. |
| «Tocca per salvare» sul bottone | iOS ha chiuso il permesso durante il download. Il file è già pronto: tocca di nuovo. |
| Il foglio non mostra «Salva video» | Apri il sito in Safari, non in Chrome. |
| «Questo link di download è scaduto» | I biglietti valgono sei ore. Cerca di nuovo il post. |

---

## Com'è fatto

```
scarico-pin-insta/
├── web/                     il sito: una pagina, un css, un js, nessuna libreria
├── deno/scarico.ts          il motore per Supabase (TypeScript)
├── deno/prova.mjs           le sue prove, si eseguono con Node
├── supabase/functions/      GENERATO: la funzione da pubblicare
├── server/                  il motore Python (yt-dlp, ffmpeg)
├── hf-space/                GENERATO: la stessa cosa in un file solo
├── tools/build_supabase.py  genera la funzione Supabase
├── tools/build_hf.py        genera il file unico Python
└── tests/                   98 prove
```

Il frontend è **uno solo** per entrambi i motori: nei file di `web/` il
segnaposto `__BASE__` viene sostituito dal server con il prefisso giusto
(vuoto in Python, `/functions/v1/scarico` su Supabase).

```bash
python3 tools/build_supabase.py && python3 tools/build_hf.py
python3 -m pytest tests/ -q     # due test falliscono apposta se te ne dimentichi
```

Cosa succede quando tocchi *Salva in galleria*: la pagina chiede il file al
server, lo legge in memoria mostrando la percentuale sul bottone, ne fa un
`File` con il tipo giusto (`video/mp4`, `image/jpeg` — se il tipo è sbagliato
iOS non offre «Salva video») e lo passa al foglio di condivisione. Se iOS nel
frattempo ha ritirato il permesso, il bottone diventa *Tocca per salvare*: il
file è già in memoria e il secondo tocco apre il foglio all'istante.

---

## Sicurezza

- **La chiave è l'unica porta.** Sta nel link, non la digiti mai. Senza
  `ACCESS_KEY` la versione Python non parte proprio.
- **Solo Pinterest e Instagram**, controllando il dominio registrato e non
  l'inizio del nome: `pinterest.qualcosaltro.com` viene rifiutato. Senza questo
  filtro il sito sarebbe un proxy per scaricare qualunque cosa.
- **I file si scaricano solo dai CDN dei due siti** (`pinimg.com`,
  `cdninstagram.com`, `fbcdn.net`), e solo in https.
- **Gli indirizzi dei CDN non arrivano mai al telefono.** Nella versione
  Supabase il biglietto di download è l'indirizzo cifrato con AES-GCM: non si
  legge e non si falsifica. Vale sei ore.
- **La chiave che cifra i biglietti non è la chiave d'accesso**, è derivata da
  quella: un solo segreto da impostare, due chiavi diverse in uso.

---

## Due righe oneste

Scarichi roba che il tuo account già vede. I video restano di chi li ha fatti:
tienili per te, e se li ripubblichi chiedi il permesso e cita l'autore.
Scaricare in massa i contenuti altrui va oltre lo scopo di questo progetto ed è
contro le condizioni d'uso dei due siti.
