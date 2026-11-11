/* Scarico - versione per Supabase Edge Functions (Deno).
 *
 * Differenze rispetto alla versione Python, e perche':
 *
 * - Niente yt-dlp: qui gira Deno, non Python. I link dei video si ricavano
 *   leggendo la pagina del post. Per Pinterest e' affidabile, per Instagram
 *   molto meno (vedi risolviInstagram).
 * - Niente ffmpeg: i contenuti con audio e video separati non si possono
 *   ricomporre. In pratica riguarda alcune storie.
 * - Nessuna memoria fra una richiesta e l'altra: le funzioni sono effimere e
 *   due richieste possono finire su istanze diverse. Per questo il biglietto
 *   di download non e' una chiave verso una tabella: e' l'indirizzo del file
 *   cifrato e autenticato dentro al biglietto stesso. Cosi' non serve stato,
 *   e l'indirizzo del CDN resta comunque illeggibile per chi lo riceve.
 */

declare const RISORSE: Record<string, string>;
declare const ICONE: Record<string, string>;

const CHIAVE_ACCESSO = Deno.env.get("ACCESS_KEY") ?? "";
const COOKIE_IG = (Deno.env.get("IG_COOKIES") ?? "").trim();
const MAX_MB = Number(Deno.env.get("MAX_FILESIZE_MB") ?? "2048");
const VERSIONE = "2.0.0";

const UA_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " +
  "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

const SCADENZA_BIGLIETTO = 6 * 60 * 60 * 1000; // sei ore

/* ------------------------------------------------------------------ tipi */

interface Elemento {
  id: string;
  source: "pinterest" | "instagram";
  kind: "video" | "immagine";
  title: string;
  filename: string;
  ext: string;
  mime: string;
  width?: number;
  height?: number;
  duration?: number;
  filesize?: number;
  merge: boolean;
  diretto: string;          // resta sul server: finisce cifrato nel biglietto
  riferimento: string;      // Referer da usare col CDN
  miniatura?: string;
}

/* --------------------------------------------------- biglietti cifrati */

const codificatore = new TextEncoder();
const decodificatore = new TextDecoder();

let chiaveAes: CryptoKey | null = null;

async function chiaveDiCifratura(): Promise<CryptoKey> {
  if (chiaveAes) return chiaveAes;
  // La chiave di cifratura e' derivata da quella d'accesso: un solo segreto
  // da impostare, ma le due non coincidono mai.
  const seme = await crypto.subtle.digest(
    "SHA-256",
    codificatore.encode("biglietti:" + (CHIAVE_ACCESSO || "aperto")),
  );
  chiaveAes = await crypto.subtle.importKey("raw", seme, "AES-GCM", false, ["encrypt", "decrypt"]);
  return chiaveAes;
}

function base64urlDa(dati: Uint8Array): string {
  let testo = "";
  for (const byte of dati) testo += String.fromCharCode(byte);
  return btoa(testo).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64urlA(testo: string): Uint8Array {
  return base64A(testo.replace(/-/g, "+").replace(/_/g, "/"));
}

function base64A(testo: string): Uint8Array {
  const grezzo = atob(testo + "=".repeat((4 - (testo.length % 4)) % 4));
  return Uint8Array.from(grezzo, (c) => c.charCodeAt(0));
}

export async function sigilla(contenuto: unknown): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cifrato = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await chiaveDiCifratura(),
    codificatore.encode(JSON.stringify(contenuto)),
  );
  const insieme = new Uint8Array(iv.length + cifrato.byteLength);
  insieme.set(iv, 0);
  insieme.set(new Uint8Array(cifrato), iv.length);
  return base64urlDa(insieme);
}

export async function apri<T>(biglietto: string): Promise<T | null> {
  try {
    const dati = base64urlA(biglietto);
    const aperto = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: dati.slice(0, 12) },
      await chiaveDiCifratura(),
      dati.slice(12),
    );
    const contenuto = JSON.parse(decodificatore.decode(aperto));
    if (typeof contenuto?.scade === "number" && contenuto.scade < Date.now()) return null;
    return contenuto as T;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------- indirizzi */

const SUFFISSI_DOPPI = new Set(["co", "com", "net", "org", "ne", "or"]);
const HOST_ESATTI: Record<string, "pinterest" | "instagram"> = {
  "pin.it": "pinterest",
  "ig.me": "instagram",
  "instagr.am": "instagram",
};
const NOMI: Record<string, "pinterest" | "instagram"> = {
  pinterest: "pinterest",
  instagram: "instagram",
};

/** Guarda il dominio registrato, non l'inizio del nome: senza questo,
 *  pinterest.qualcosaltro.com passerebbe. */
export function riconosci(host: string | null): "pinterest" | "instagram" | null {
  if (!host) return null;
  let h = host.toLowerCase().replace(/^\.+|\.+$/g, "");
  if (h.startsWith("www.")) h = h.slice(4);
  if (HOST_ESATTI[h]) return HOST_ESATTI[h];
  const parti = h.split(".");
  if (parti.length < 2) return null;
  const nome = parti.length >= 3 && SUFFISSI_DOPPI.has(parti[parti.length - 2]) &&
      parti[parti.length - 1].length === 2
    ? parti[parti.length - 3]
    : parti[parti.length - 2];
  return NOMI[nome] ?? null;
}

/** Il foglio di condivisione di iOS incolla spesso titolo e link insieme. */
export function primoLinkUtile(testo: string): { url: string; fonte: "pinterest" | "instagram" } {
  const candidati = testo.match(/https?:\/\/[^\s<>"']+/gi) ??
    (testo.trim() ? ["https://" + testo.trim().split(/\s+/)[0]] : []);
  for (const grezzo of candidati) {
    const pulito = grezzo.replace(/[).,;'"]+$/, "");
    try {
      const u = new URL(pulito);
      const fonte = riconosci(u.hostname);
      if (fonte) return { url: u.origin + u.pathname, fonte };
    } catch { /* candidato non valido, passo al prossimo */ }
  }
  throw new ErroreUtente(
    "Questo link non e’ di Pinterest o Instagram. Copia l’indirizzo di un pin, di un reel o di un post.",
    400,
  );
}

/** Da dove accettiamo di scaricare i file veri. */
const CDN_AMMESSI = [".pinimg.com", ".cdninstagram.com", ".fbcdn.net", ".pinterest.com", ".instagram.com"];

export function cdnAmmesso(indirizzo: string): boolean {
  try {
    const u = new URL(indirizzo);
    if (u.protocol !== "https:") return false;
    return CDN_AMMESSI.some((suffisso) => u.hostname.endsWith(suffisso));
  } catch {
    return false;
  }
}

/* ------------------------------------------------------------- utilita' */

class ErroreUtente extends Error {
  stato: number;
  constructor(messaggio: string, stato = 400) {
    super(messaggio);
    this.stato = stato;
  }
}

const MIME_PER_ESTENSIONE: Record<string, string> = {
  mp4: "video/mp4", m4v: "video/x-m4v", mov: "video/quicktime", webm: "video/webm",
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", gif: "image/gif",
};

export function nomePulito(titolo: string): string {
  const pulito = (titolo || "").replace(/[\\/:*?"<>|\x00-\x1f]+/g, " ").replace(/\s+/g, " ").trim();
  return (pulito.length > 70 ? pulito.slice(0, 70).trim() : pulito).replace(/^[.\-_ ]+|[.\-_ ]+$/g, "") ||
    "senza titolo";
}

export function componiNome(fonte: string, titolo: string, ext: string): string {
  const coda = Math.random().toString(36).slice(2, 8);
  return `${fonte} - ${nomePulito(titolo)} - ${coda}.${ext}`;
}

export function estensioneDa(indirizzo: string, difetto: string): string {
  const pezzo = indirizzo.split("?")[0].split("#")[0];
  const ext = pezzo.includes(".") ? pezzo.split(".").pop()!.toLowerCase() : "";
  return MIME_PER_ESTENSIONE[ext] ? ext : difetto;
}

export function decodificaHtml(testo: string): string {
  return testo
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&#x27;/gi, "'")
    .replace(/&nbsp;/g, " ");
}

export function meta(html: string, proprieta: string): string | null {
  const diretto = new RegExp(
    `<meta[^>]+(?:property|name)=["']${proprieta}["'][^>]*content=["']([^"']+)["']`, "i",
  ).exec(html);
  if (diretto) return decodificaHtml(diretto[1]);
  const rovescio = new RegExp(
    `<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["']${proprieta}["']`, "i",
  ).exec(html);
  return rovescio ? decodificaHtml(rovescio[1]) : null;
}

async function scaricaPagina(indirizzo: string, cookie?: string): Promise<{ html: string; finale: string }> {
  const risposta = await fetch(indirizzo, {
    headers: {
      "User-Agent": UA_IPHONE,
      "Accept": "text/html,application/xhtml+xml",
      "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
      ...(cookie ? { Cookie: cookie } : {}),
    },
    redirect: "follow",
  });
  if (!risposta.ok) {
    throw new ErroreUtente(
      `La pagina non risponde (codice ${risposta.status}). Il post potrebbe essere stato rimosso o essere privato.`,
      502,
    );
  }
  return { html: await risposta.text(), finale: risposta.url || indirizzo };
}

/* ------------------------------------------------------------ Pinterest */

async function risolviPinterest(indirizzo: string): Promise<Elemento[]> {
  const { html, finale } = await scaricaPagina(indirizzo);
  const titolo = meta(html, "og:title") ?? "pin";

  // I video dei pin stanno su v1.pinimg.com. Nei meta tag c'e' quello
  // principale, ma nel JSON della pagina ci sono tutte le qualita'.
  const candidati = new Set<string>();
  for (const chiave of ["og:video:secure_url", "og:video:url", "og:video"]) {
    const trovato = meta(html, chiave);
    if (trovato) candidati.add(trovato);
  }
  for (const trovato of html.matchAll(/https:\\?\/\\?\/v1\.pinimg\.com\\?\/videos\\?\/[^"'\\\s]+\.mp4/gi)) {
    candidati.add(trovato[0].replace(/\\\//g, "/"));
  }

  const video = [...candidati].filter(cdnAmmesso).sort((a, b) => qualita(b) - qualita(a))[0];
  const immagine = meta(html, "og:image");

  if (video) {
    const ext = estensioneDa(video, "mp4");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "pinterest",
      kind: "video",
      title: titolo,
      filename: componiNome("pinterest", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "video/mp4",
      merge: false,
      diretto: video,
      riferimento: finale,
      miniatura: immagine ?? undefined,
    }];
  }

  if (immagine && cdnAmmesso(immagine)) {
    const piena = immagine.replace(/\/\d+x\d*\//, "/originals/");
    const ext = estensioneDa(piena, "jpg");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "pinterest",
      kind: "immagine",
      title: titolo,
      filename: componiNome("pinterest", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "image/jpeg",
      merge: false,
      diretto: piena,
      riferimento: finale,
      miniatura: immagine,
    }];
  }

  throw new ErroreUtente("In questo pin non ho trovato ne’ un video ne’ una foto.", 404);
}

/** Le qualita' stanno nel percorso: .../720p/..., .../480p/... */
export function qualita(indirizzo: string): number {
  const trovato = /\/(\d{3,4})p\//.exec(indirizzo);
  return trovato ? Number(trovato[1]) : 0;
}

/* ------------------------------------------------------------ Instagram */

async function risolviInstagram(indirizzo: string): Promise<Elemento[]> {
  const codice = /\/(?:reel|reels|p|tv)\/([A-Za-z0-9_-]+)/.exec(indirizzo)?.[1];
  if (!codice) {
    throw new ErroreUtente(
      "Di Instagram so leggere i link di un post, di un reel o di un video (quelli con /p/, /reel/ o /tv/).",
      400,
    );
  }

  // 1. L'API interna: a volte risponde anche senza essere loggati, e quando
  //    risponde da' tutti gli elementi di un carosello.
  const daApi = await provaApiInstagram(codice).catch(() => null);
  if (daApi && daApi.length) return daApi;

  // 2. Altrimenti la pagina. Senza cookie Instagram la nega quasi sempre.
  const { html, finale } = await scaricaPagina(
    `https://www.instagram.com/p/${codice}/`,
    COOKIE_IG || undefined,
  );
  const titolo = meta(html, "og:title") ?? "instagram";
  const video = meta(html, "og:video:secure_url") ?? meta(html, "og:video") ??
    html.match(/"video_url":"([^"]+)"/)?.[1]?.replace(/\\u0026/g, "&").replace(/\\\//g, "/");
  const immagine = meta(html, "og:image") ??
    html.match(/"display_url":"([^"]+)"/)?.[1]?.replace(/\\u0026/g, "&").replace(/\\\//g, "/");

  if (video && cdnAmmesso(video)) {
    const ext = estensioneDa(video, "mp4");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "instagram",
      kind: "video",
      title: titolo,
      filename: componiNome("instagram", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "video/mp4",
      merge: false,
      diretto: video,
      riferimento: finale,
      miniatura: immagine ?? undefined,
    }];
  }
  if (immagine && cdnAmmesso(immagine)) {
    const ext = estensioneDa(immagine, "jpg");
    return [{
      id: crypto.randomUUID().slice(0, 8),
      source: "instagram",
      kind: "immagine",
      title: titolo,
      filename: componiNome("instagram", titolo, ext),
      ext,
      mime: MIME_PER_ESTENSIONE[ext] ?? "image/jpeg",
      merge: false,
      diretto: immagine,
      riferimento: finale,
      miniatura: immagine,
    }];
  }

  throw new ErroreUtente(
    COOKIE_IG
      ? "Instagram non mi ha dato il contenuto: il post potrebbe essere privato, oppure i cookie sono scaduti."
      : "Instagram chiede di essere loggato per questo contenuto. Aggiungi il segreto IG_COOKIES alla funzione.",
    403,
  );
}

/** Lo shortcode di Instagram e' il numero del contenuto in base64. */
export function idDaCodice(codice: string): string | null {
  const alfabeto = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  let numero = 0n;
  for (const carattere of codice.slice(0, 11)) {
    const posizione = alfabeto.indexOf(carattere);
    if (posizione < 0) return null;
    numero = numero * 64n + BigInt(posizione);
  }
  return numero > 0n ? numero.toString() : null;
}

async function provaApiInstagram(codice: string): Promise<Elemento[] | null> {
  const id = idDaCodice(codice);
  if (!id) return null;
  const risposta = await fetch(`https://www.instagram.com/api/v1/media/${id}/info/`, {
    headers: {
      "User-Agent": UA_IPHONE,
      "X-IG-App-ID": "936619743392459",
      "Accept": "application/json",
      ...(COOKIE_IG ? { Cookie: COOKIE_IG } : {}),
    },
  });
  if (!risposta.ok) return null;
  const dati = await risposta.json().catch(() => null);
  const posts = dati?.items;
  if (!Array.isArray(posts) || !posts.length) return null;

  const pezzi: Elemento[] = [];
  for (const post of posts) {
    const figli = post.carousel_media ?? [post];
    const didascalia = post.caption?.text ?? "instagram";
    for (const figlio of figli) {
      const video = figlio.video_versions?.[0]?.url;
      const foto = figlio.image_versions2?.candidates?.[0]?.url;
      const scelto = video ?? foto;
      if (!scelto || !cdnAmmesso(scelto)) continue;
      const ext = estensioneDa(scelto, video ? "mp4" : "jpg");
      pezzi.push({
        id: crypto.randomUUID().slice(0, 8),
        source: "instagram",
        kind: video ? "video" : "immagine",
        title: didascalia,
        filename: componiNome("instagram", didascalia, ext),
        ext,
        mime: MIME_PER_ESTENSIONE[ext] ?? (video ? "video/mp4" : "image/jpeg"),
        width: figlio.original_width,
        height: figlio.original_height,
        duration: figlio.video_duration,
        merge: false,
        diretto: scelto,
        riferimento: `https://www.instagram.com/p/${codice}/`,
        miniatura: foto ?? undefined,
      });
    }
  }
  return pezzi.length ? pezzi : null;
}

/* --------------------------------------------------------------- risposte */

const NIENTE_CACHE = { "Cache-Control": "no-store, max-age=0" };

function json(corpo: unknown, stato = 200, intestazioni: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(corpo), {
    status: stato,
    headers: { "Content-Type": "application/json; charset=utf-8", ...NIENTE_CACHE, ...intestazioni },
  });
}

function testo(contenuto: string, tipo: string, base: string): Response {
  return new Response(contenuto.replaceAll("__BASE__", base), {
    headers: { "Content-Type": tipo, ...NIENTE_CACHE },
  });
}

export function disposizione(nome: string): string {
  const ascii = nome.normalize("NFKD").replace(/[^\x20-\x7e]/g, "").replace(/"/g, "") || "file";
  return `attachment; filename="${ascii}"; filename*=UTF-8''${encodeURIComponent(nome)}`;
}

function chiaveValida(url: URL, intestazioni: Headers): boolean {
  if (!CHIAVE_ACCESSO) return true; // nessuna chiave impostata: sito aperto
  const fornita = intestazioni.get("x-chiave") ?? url.searchParams.get("k") ?? "";
  if (fornita.length !== CHIAVE_ACCESSO.length) return false;
  // Confronto a tempo costante, cosi' la chiave non si indovina a tentativi misurati.
  let differenza = 0;
  for (let i = 0; i < fornita.length; i++) {
    differenza |= fornita.charCodeAt(i) ^ CHIAVE_ACCESSO.charCodeAt(i);
  }
  return differenza === 0;
}

/* ------------------------------------------------- consegna dei file */

interface Biglietto {
  u: string;      // indirizzo sul CDN
  n: string;      // nome del file
  m: string;      // tipo
  r: string;      // riferimento
  scade: number;
}

async function consegna(biglietto: string, richiesta: Request, daSalvare: boolean, tettoByte?: number) {
  const dati = await apri<Biglietto>(biglietto);
  if (!dati || !cdnAmmesso(dati.u)) {
    return json({ detail: "Questo link di download e’ scaduto. Cerca di nuovo il post." }, 403);
  }

  const intestazioni: Record<string, string> = { "User-Agent": UA_IPHONE, Referer: dati.r };
  const intervallo = richiesta.headers.get("range");
  if (intervallo) intestazioni["Range"] = intervallo;

  const sorgente = await fetch(dati.u, { headers: intestazioni }).catch(() => null);
  if (!sorgente) return json({ detail: "Il server di Pinterest/Instagram non risponde." }, 504);
  if (!sorgente.ok && sorgente.status !== 206) {
    return json({
      detail: `Il file non e’ piu’ disponibile (il CDN ha risposto ${sorgente.status}). Cerca di nuovo il post.`,
    }, 502);
  }

  const lunghezza = Number(sorgente.headers.get("content-length") ?? "0");
  const tetto = tettoByte ?? (MAX_MB > 0 ? MAX_MB * 1024 * 1024 : 0);
  if (tetto && lunghezza > tetto) {
    await sorgente.body?.cancel();
    return json({ detail: `Il file pesa ${Math.round(lunghezza / 1048576)} MB e supera il limite.` }, 413);
  }

  const uscita = new Headers(NIENTE_CACHE);
  for (const nome of ["content-length", "content-range", "accept-ranges"]) {
    const valore = sorgente.headers.get(nome);
    if (valore) uscita.set(nome, valore);
  }
  uscita.set("Content-Type", sorgente.headers.get("content-type") ?? dati.m);
  if (daSalvare) uscita.set("Content-Disposition", disposizione(dati.n));

  // Il corpo passa in streaming: niente resta in memoria sulla funzione.
  return new Response(sorgente.body, { status: sorgente.status, headers: uscita });
}

/* ------------------------------------------------------------- manifest */

function manifest(base: string, chiave: string | null): Response {
  // iOS puo' usare start_url quando aggiunge il sito alla schermata Home:
  // se li' dentro la chiave non ci fosse, l'icona aprirebbe un indirizzo nudo.
  const partenza = chiave ? `${base}/?k=${encodeURIComponent(chiave)}` : `${base}/`;
  return json({
    name: "Scarico Pin e Insta",
    short_name: "Scarico",
    description: "Salva in galleria i video e le foto di Pinterest e Instagram.",
    lang: "it",
    start_url: partenza,
    scope: base + "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#0c0a12",
    theme_color: "#0c0a12",
    icons: [
      { src: `${base}/icons/icon-180.png`, sizes: "180x180", type: "image/png" },
      { src: `${base}/icons/icon-192.png`, sizes: "192x192", type: "image/png", purpose: "any" },
      { src: `${base}/icons/icon-512.png`, sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  }, 200, { "Content-Type": "application/manifest+json" });
}

/* ------------------------------------------------------------- risoluzione */

async function risolvi(grezzo: string): Promise<Elemento[]> {
  const { url, fonte } = primoLinkUtile(grezzo);
  const pezzi = fonte === "pinterest" ? await risolviPinterest(url) : await risolviInstagram(url);
  if (!pezzi.length) throw new ErroreUtente("Non ho trovato niente da scaricare.", 404);
  return pezzi;
}

async function conBiglietti(pezzi: Elemento[], base: string) {
  const fuori = [];
  for (const pezzo of pezzi) {
    const scade = Date.now() + SCADENZA_BIGLIETTO;
    const biglietto = await sigilla(
      { u: pezzo.diretto, n: pezzo.filename, m: pezzo.mime, r: pezzo.riferimento, scade } as Biglietto,
    );
    const voce: Record<string, unknown> = {
      id: pezzo.id,
      source: pezzo.source,
      kind: pezzo.kind,
      title: pezzo.title,
      filename: pezzo.filename,
      ext: pezzo.ext,
      mime: pezzo.mime,
      width: pezzo.width ?? null,
      height: pezzo.height ?? null,
      duration: pezzo.duration ?? null,
      filesize: pezzo.filesize ?? null,
      merge: pezzo.merge,
      url_file: `/api/file?t=${encodeURIComponent(biglietto)}`,
    };
    if (pezzo.miniatura && cdnAmmesso(pezzo.miniatura)) {
      const bigliettoThumb = await sigilla(
        { u: pezzo.miniatura, n: "anteprima.jpg", m: "image/jpeg", r: pezzo.riferimento, scade } as Biglietto,
      );
      voce.url_thumb = `/api/thumb?t=${encodeURIComponent(bigliettoThumb)}`;
    }
    fuori.push(voce);
  }
  return fuori;
}

/* ------------------------------------------------------------------ server */

Deno.serve(async (richiesta: Request) => {
  const url = new URL(richiesta.url);

  // Su Supabase la funzione vive sotto /functions/v1/<nome>: tutto il sito
  // deve sapere questo prefisso, altrimenti i suoi link cadono nel vuoto.
  const prefisso = url.pathname.match(/^\/functions\/v1\/[^/]+/)?.[0] ?? "";
  const percorso = url.pathname.slice(prefisso.length) || "/";
  const base = prefisso;

  try {
    if (percorso === "/" || percorso === "") {
      return testo(RISORSE["index.html"], "text/html; charset=utf-8", base);
    }
    if (percorso === "/style.css") return testo(RISORSE["style.css"], "text/css; charset=utf-8", base);
    if (percorso === "/app.js") {
      return testo(RISORSE["app.js"], "application/javascript; charset=utf-8", base);
    }
    if (percorso === "/manifest.webmanifest") {
      const k = url.searchParams.get("k");
      return manifest(base, k && chiaveValida(url, richiesta.headers) ? k : null);
    }
    if (percorso.startsWith("/icons/")) {
      const nome = percorso.slice("/icons/".length);
      const dati = ICONE[nome];
      if (!dati) return new Response("non trovata", { status: 404 });
      // Le icone viaggiano dentro al programma in base64: il PNG si ricompone qui.
      const byte = base64A(dati);
      return new Response(byte.buffer as ArrayBuffer, {
        headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" },
      });
    }
    if (percorso === "/api/health") {
      return json({ ok: true, version: VERSIONE, motore: "deno" });
    }

    // Da qui in poi serve la chiave.
    if (!chiaveValida(url, richiesta.headers)) {
      return json({
        detail: "Questo indirizzo non e’ completo. Apri il tuo link personale, quello che finisce con ?k=...",
      }, 401);
    }

    if (percorso === "/api/me") {
      return json({
        versione: VERSIONE,
        ffmpeg: false,
        cookie_instagram: Boolean(COOKIE_IG),
        limite_mb: MAX_MB,
      });
    }

    if (percorso === "/api/resolve" && richiesta.method === "POST") {
      const corpo = await richiesta.json().catch(() => ({}));
      const link = String(corpo?.url ?? "").trim();
      if (!link) return json({ detail: "Manca il link." }, 400);
      const pezzi = await risolvi(link);
      return json({ elementi: await conBiglietti(pezzi, base) });
    }

    if (percorso === "/api/file") {
      const biglietto = url.searchParams.get("t") ?? "";
      return await consegna(biglietto, richiesta, url.searchParams.get("apri") !== "1");
    }

    if (percorso === "/api/thumb") {
      const biglietto = url.searchParams.get("t") ?? "";
      return await consegna(biglietto, richiesta, false, 8 * 1024 * 1024);
    }

    if (percorso === "/api/shortcut") {
      const link = url.searchParams.get("u") ?? "";
      if (!link) return json({ detail: "Manca il link." }, 400);
      const pezzi = await conBiglietti(await risolvi(link), base);
      const primo = pezzi[0];
      return json({
        ok: true,
        download: url.origin + base + String(primo.url_file),
        nome_file: primo.filename,
        tipo: primo.mime,
        quanti: pezzi.length,
        tutti: pezzi.map((p) => ({
          download: url.origin + base + String(p.url_file),
          nome_file: p.filename,
          tipo: p.mime,
        })),
      });
    }

    return json({ detail: "Non esiste niente a questo indirizzo." }, 404);
  } catch (errore) {
    if (errore instanceof ErroreUtente) return json({ detail: errore.message }, errore.stato);
    console.error("errore non previsto:", errore);
    return json({ detail: "Qualcosa e’ andato storto sul server." }, 500);
  }
});
