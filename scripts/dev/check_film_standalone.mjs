/**
 * Check that a standalone film really is standalone.
 *
 * The point of the single-file build is that it can be sent to someone with no
 * folder beside it, so the only honest test is to open it from a directory
 * where that folder does not exist and confirm it still talks. Opening it in
 * place would pass even if the audio were still being fetched from the sibling
 * directory, which is the exact failure this is here to catch.
 *
 * Usage:
 *   node scripts/dev/check_film_standalone.mjs
 */
import { chromium } from 'playwright';
import { mkdtemp, copyFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = join(ROOT, 'output');

const films = (await readdir(OUT)).filter((f) => f.endsWith('_standalone.html'));
if (films.length === 0) {
  console.error('no standalone films in output/ — run scripts/build_film_standalone.py');
  process.exit(1);
}

let failures = 0;
const browser = await chromium.launch();

for (const film of films) {
  // Somewhere with nothing else in it, so a sibling directory cannot rescue it.
  const alone = await mkdtemp(join(tmpdir(), 'standalone-'));
  await copyFile(join(OUT, film), join(alone, film));

  const page = await browser.newPage({ viewport: { width: 1280, height: 760 } });
  const requested = [];
  page.on('request', (r) => requested.push(r.url()));

  await page.addInitScript(() => {
    window.__played = [];
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function patched() {
      window.__played.push(String(this.src || '').slice(0, 24));
      return play.apply(this, arguments);
    };
  });

  await page.goto(`file://${join(alone, film)}`);
  await page.waitForFunction(() => window.__stage != null, { timeout: 30_000 });
  await page.waitForTimeout(4000);

  const played = await page.evaluate(() => window.__played);
  const inline = played.filter((s) => s.startsWith('data:audio'));
  const fetched = requested.filter((u) => /_vo\/|\.mp3$/.test(u));

  const ok = inline.length > 0 && fetched.length === 0;
  failures += ok ? 0 : 1;
  console.log(
    `  ${ok ? 'ok  ' : 'FAIL'} ${film} — ${inline.length} clip(s) played inline, ` +
      `${fetched.length} fetched from disk`
  );
  if (fetched.length) console.log(`        still reaching for ${fetched[0]}`);
  if (played.length === 0) console.log('        nothing played at all');
  await page.close();
}

await browser.close();
console.log(failures ? `\n${failures} film(s) are not self-contained` : '\nevery standalone film plays on its own');
process.exit(failures ? 1 : 0);
