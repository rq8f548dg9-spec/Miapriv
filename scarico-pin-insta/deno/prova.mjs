/* Prove della funzione Deno, eseguite con Node.
 * Node sa togliere i tipi da solo (--experimental-strip-types), quindi non
 * serve compilare niente. Deno non c'e': ne fornisco un finto minimo. */

import assert from 'node:assert/strict';

globalThis.Deno = { env: { get: () => undefined }, serve: () => {} };

const m = await import('./scarico.ts');
let fatte = 0;
const prova = (nome, f) => { f(); fatte++; console.log('  ok  ' + nome); };
const provaAsync = async (nome, f) => { await f(); fatte++; console.log('  ok  ' + nome); };

console.log('riconoscimento degli host');
prova('i domini buoni passano', () => {
  for (const h of ['www.pinterest.com', 'pinterest.it', 'it.pinterest.com', 'pinterest.co.uk']) {
    assert.equal(m.riconosci(h), 'pinterest', h);
  }
  for (const h of ['instagram.com', 'www.instagram.com', 'instagr.am', 'ig.me']) {
    assert.equal(m.riconosci(h), 'instagram', h);
  }
  assert.equal(m.riconosci('pin.it'), 'pinterest');
});
prova('i sosia vengono rifiutati', () => {
  for (const h of ['pinterest.evil.com', 'instagram.com.attacco.net', 'mio-pinterest.com',
                   'youtube.com', 'instagram', null, '']) {
    assert.equal(m.riconosci(h), null, String(h));
  }
});

console.log('estrazione del link da un testo condiviso');
prova('prende il primo link utile', () => {
  const r = m.primoLinkUtile('Guarda qui https://www.pinterest.it/pin/123/?utm_source=x');
  assert.equal(r.url, 'https://www.pinterest.it/pin/123/');
  assert.equal(r.fonte, 'pinterest');
});
prova('salta i link di altri siti', () => {
  const r = m.primoLinkUtile('vedi https://example.com/x e https://pin.it/abc');
  assert.equal(r.url, 'https://pin.it/abc');
});
prova('accetta un link senza schema', () => {
  assert.equal(m.primoLinkUtile('instagram.com/reel/XYZ/').url, 'https://instagram.com/reel/XYZ/');
});
prova('rifiuta cio che non e Pinterest o Instagram', () => {
  assert.throws(() => m.primoLinkUtile('https://youtube.com/watch?v=1'), /Pinterest/);
  assert.throws(() => m.primoLinkUtile('   '), /Pinterest/);
});

console.log('da dove accettiamo i file');
prova('solo i CDN dei due siti, e solo in https', () => {
  assert.equal(m.cdnAmmesso('https://v1.pinimg.com/videos/a.mp4'), true);
  assert.equal(m.cdnAmmesso('https://scontent.cdninstagram.com/v/a.mp4'), true);
  assert.equal(m.cdnAmmesso('https://x.fbcdn.net/a.mp4'), true);
  assert.equal(m.cdnAmmesso('http://v1.pinimg.com/a.mp4'), false, 'http non deve passare');
  assert.equal(m.cdnAmmesso('https://pinimg.com.evil.net/a.mp4'), false);
  assert.equal(m.cdnAmmesso('https://127.0.0.1/a.mp4'), false);
  assert.equal(m.cdnAmmesso('non-un-indirizzo'), false);
});

console.log('nomi dei file');
prova('ripulisce i caratteri che rompono i filesystem', () => {
  assert.equal(m.nomePulito('Pasta: al / forno "2026"'), 'Pasta al forno 2026');
  assert.equal(m.nomePulito(''), 'senza titolo');
  assert.ok(m.nomePulito('x'.repeat(200)).length <= 70);
  assert.equal(m.nomePulito('Idée déco salón'), 'Idée déco salón');
});
prova('il nome finisce con la giusta estensione', () => {
  assert.match(m.componiNome('pinterest', 'Cucina', 'mp4'), /^pinterest - Cucina - [a-z0-9]{6}\.mp4$/);
});
prova('Safari riceve il nome in due versioni', () => {
  const d = m.disposizione('pinterest - Caffè in città - abcdef.mp4');
  assert.ok(d.startsWith('attachment;'));
  assert.ok(d.includes('filename="pinterest - Caffe in citta - abcdef.mp4"'));
  assert.ok(d.includes("filename*=UTF-8''"));
  assert.ok(d.includes('Caff%C3%A8'));
});

console.log('lettura della pagina');
prova('legge i meta tag in entrambi gli ordini', () => {
  assert.equal(m.meta('<meta property="og:title" content="Ciao &amp; via">', 'og:title'), 'Ciao & via');
  assert.equal(m.meta('<meta content="https://x/v.mp4" property="og:video">', 'og:video'), 'https://x/v.mp4');
  assert.equal(m.meta('<html>', 'og:title'), null);
});
prova('sceglie la qualita piu alta', () => {
  assert.equal(m.qualita('https://v1.pinimg.com/videos/mc/720p/a.mp4'), 720);
  assert.ok(m.qualita('.../720p/a.mp4') > m.qualita('.../480p/a.mp4'));
  assert.equal(m.qualita('https://v1.pinimg.com/a.mp4'), 0);
});
prova('indovina l estensione', () => {
  assert.equal(m.estensioneDa('https://x/a.mp4?token=1', 'mp4'), 'mp4');
  assert.equal(m.estensioneDa('https://x/a.webp', 'jpg'), 'webp');
  assert.equal(m.estensioneDa('https://x/senza-estensione', 'jpg'), 'jpg');
  assert.equal(m.estensioneDa('https://x/a.exe', 'mp4'), 'mp4', 'un estensione sconosciuta ricade sul difetto');
});

console.log('codice del post di Instagram');
prova('decodifica lo shortcode nel numero del contenuto', () => {
  const alfabeto = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  const codifica = (n) => { let s = ''; let v = BigInt(n);
    while (v > 0n) { s = alfabeto[Number(v % 64n)] + s; v /= 64n; } return s; };
  for (const id of ['3245678901234567890', '1234567890', '999']) {
    assert.equal(m.idDaCodice(codifica(id)), id, id);
  }
  assert.equal(m.idDaCodice('###'), null);
});

console.log('biglietti di download');
await provaAsync('un biglietto si richiude e si riapre', async () => {
  const dentro = { u: 'https://v1.pinimg.com/videos/a.mp4', n: 'f.mp4', m: 'video/mp4',
                   r: 'https://pinterest.com/', scade: Date.now() + 60000 };
  const biglietto = await m.sigilla(dentro);
  assert.ok(!biglietto.includes('pinimg'), 'l indirizzo del CDN non deve essere leggibile');
  assert.deepEqual(await m.apri(biglietto), dentro);
});
await provaAsync('un biglietto manomesso non si apre', async () => {
  const b = await m.sigilla({ u: 'https://v1.pinimg.com/a.mp4', scade: Date.now() + 60000 });
  const rotto = b.slice(0, -4) + (b.slice(-4) === 'AAAA' ? 'BBBB' : 'AAAA');
  assert.equal(await m.apri(rotto), null);
  assert.equal(await m.apri('spazzatura'), null);
  assert.equal(await m.apri(''), null);
});
await provaAsync('un biglietto scaduto non vale', async () => {
  const b = await m.sigilla({ u: 'https://v1.pinimg.com/a.mp4', scade: Date.now() - 1 });
  assert.equal(await m.apri(b), null);
});

console.log('\n' + fatte + ' prove superate');
