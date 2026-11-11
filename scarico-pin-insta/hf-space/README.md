---
title: Scarico
emoji: "❤"
colorFrom: purple
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
short_description: Salva in galleria i video e le foto di Pinterest e Instagram
---

# Scarico

Sito privato per salvare sull'iPhone i video e le foto di Pinterest e Instagram.

**Prima di usarlo** metti in Settings > Variables and secrets un secret chiamato
`ACCESS_KEY` con dentro una chiave lunga a tua scelta. Senza, lo Space non parte:
e' voluto, altrimenti chiunque trovi l'indirizzo potrebbe usarlo.

Poi apri `https://<il-tuo-space>.hf.space/?k=LA-TUA-CHIAVE` e, da Safari,
Condividi > Aggiungi a Home.

Secret facoltativo: `IG_COOKIES`, il contenuto di un `cookies.txt` esportato da
instagram.com mentre sei loggato. Serve per i contenuti che Instagram non mostra
agli sconosciuti.

Il codice di questo file e' generato: il progetto vero sta altrove, in
`scarico-pin-insta/`.
