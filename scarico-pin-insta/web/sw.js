/* Service worker: tiene in cache solo il guscio del sito.
   Le API e i file video non vengono MAI messi in cache: sono privati,
   pesanti e i biglietti di download scadono. */
'use strict';

const CACHE = 'scarico-v2';
const GUSCIO = [
  '/',
  '/style.css',
  '/app.js',
  '/icons/icon-180.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
];

self.addEventListener('install', (evento) => {
  evento.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(GUSCIO)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches
      .keys()
      .then((nomi) => Promise.all(nomi.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evento) => {
  const richiesta = evento.request;
  const url = new URL(richiesta.url);

  // Fuori dal nostro sito, non GET, o rotte dinamiche: lascio passare.
  if (richiesta.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  // Il manifest cambia con la chiave: in cache diventerebbe quello sbagliato.
  if (url.pathname === '/manifest.webmanifest') return;

  // Navigazione: prima la rete, la cache solo se la rete non c'e'.
  if (richiesta.mode === 'navigate') {
    evento.respondWith(fetch(richiesta).catch(() => caches.match('/')));
    return;
  }

  // Guscio: prima la cache, e intanto si aggiorna in silenzio.
  evento.respondWith(
    caches.match(richiesta).then((salvata) => {
      const dallaRete = fetch(richiesta)
        .then((risposta) => {
          if (risposta && risposta.ok && risposta.type === 'basic') {
            const copia = risposta.clone();
            caches.open(CACHE).then((cache) => cache.put(richiesta, copia));
          }
          return risposta;
        })
        .catch(() => salvata);
      return salvata || dallaRete;
    })
  );
});
