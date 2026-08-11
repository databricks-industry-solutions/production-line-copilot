/**
 * Load the bundled film in a real browser and prove it still plays.
 *
 * The film is 85 KB of JSX transformed by Babel in the page, inside a bundle
 * whose payload is gzipped. Nothing in the Python build can tell you whether
 * the result renders — a JSX typo survives the round-trip check intact and
 * fails silently at Babel time, leaving a black canvas and a console error
 * nobody reads. This opens the file the way a viewer would.
 *
 * Checks the stage mounts, the timeline advances, seeking works, and the
 * chapter agenda lists what it should. Writes a screenshot per chapter to
 * `output/film-smoke/` so the frames can be eyeballed.
 *
 * Usage:  node scripts/dev/smoke_film.mjs [--shots]
 */
import { chromium } from 'playwright';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { mkdirSync } from 'node:fs';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

const FILMS = {
  short: { file: 'prodline_copilot_film.html', key: 'prodline-copilot', minChapters: 5 },
  deep: { file: 'prodline_copilot_deep_dive.html', key: 'prodline-copilot-deep-dive', minChapters: 14 },
};
const which = process.argv.includes('--deep-dive') ? 'deep' : 'short';
const target = FILMS[which];

const FILM = join(ROOT, 'docs', 'media', target.file);
const SHOTS = join(ROOT, 'output', 'film-smoke', which);

const wantShots = process.argv.includes('--shots');
console.log(`\n${target.file}`);
const failures = [];
const check = (ok, label) => {
  console.log(`${ok ? '  ok  ' : ' FAIL '} ${label}`);
  if (!ok) failures.push(label);
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

// The bundler runtime probes its own URL with fetch(), which no browser allows
// under file://. It has always done this, the film plays regardless, and the
// alternative is serving the file over HTTP — which would test something other
// than the way people actually open it.
const KNOWN_FILE_SCHEME_NOISE = [/Fetch API cannot load file:/, /net::ERR_FILE_NOT_FOUND/];

const KEY = `${target.key}:t`;

const errors = [];
const record = (text) => {
  if (!KNOWN_FILE_SCHEME_NOISE.some((re) => re.test(text))) errors.push(text);
};
page.on('console', (m) => {
  if (m.type() === 'error') record(m.text());
});
page.on('pageerror', (e) => record(String(e)));

await page.goto(pathToFileURL(FILM).href);

// The stage exposes a seek hook once React has mounted and Babel has run.
await page.waitForFunction(() => window.__stage != null, { timeout: 30_000 });
const duration = await page.evaluate(() => window.__stage.duration);
check(duration > 0, `stage mounted, duration ${duration}s`);

// The clock advances on its own.
const t0 = await page.evaluate((k) => parseFloat(localStorage.getItem(k) || '0'), KEY);
await page.waitForTimeout(1200);
const t1 = await page.evaluate((k) => parseFloat(localStorage.getItem(k) || '0'), KEY);
check(t1 > t0, `timeline advances (${t0.toFixed(2)} -> ${t1.toFixed(2)})`);

// Chapters: the button carries the current chapter, the agenda lists them all.
const chapterButton = page.getByTitle('Chapters');
check((await chapterButton.count()) === 1, 'chapter button is present');
await chapterButton.click();
const items = page.locator('button', { hasText: /.+/ });
const agenda = await page.evaluate(() => {
  const open = document.querySelectorAll('button');
  return Array.from(open)
    .map((b) => b.textContent.trim())
    .filter((t) => /^\d+:\d\d/.test(t));
});
check(agenda.length >= target.minChapters, `agenda lists ${agenda.length} chapters (want >= ${target.minChapters})`);
console.log(agenda.map((a) => `        ${a}`).join('\n'));

// Clicking a chapter seeks to it. Addressed by position rather than by
// accessible name: the row concatenates its timecode, title and badge into one
// string, so a name-based locator is matching an implementation detail.
await page.evaluate(() => window.__stage.setPlaying(false));
const jumpIndex = agenda.length - 2;
if (jumpIndex > 0) {
  const label = agenda[jumpIndex];
  await page.evaluate((i) => {
    const rows = Array.from(document.querySelectorAll('button')).filter((b) => /^\d+:\d\d/.test(b.textContent.trim()));
    rows[i].click();
  }, jumpIndex);
  await page.waitForTimeout(300);
  const seeked = await page.evaluate((k) => parseFloat(localStorage.getItem(k) || '0'), KEY);
  // Minutes are not fixed-width once a film runs past ten of them.
  const [, mm, ss] = label.match(/^(\d+):(\d\d)/);
  const want = Number(mm) * 60 + Number(ss);
  check(
    Math.abs(seeked - want) < 1.5,
    `clicking "${label.slice(0, 30)}" seeks to ${want}s (landed ${seeked.toFixed(1)}s)`
  );
}

if (wantShots) {
  mkdirSync(SHOTS, { recursive: true });
  const marks = await page.evaluate(() => window.__stage.duration);
  await page.keyboard.press('Escape');
  for (let i = 0; i < 8; i += 1) {
    const t = (marks / 8) * i;
    await page.evaluate((v) => window.__stage.setTime(v), t);
    await page.waitForTimeout(320);
    await page.screenshot({ path: join(SHOTS, `t${String(Math.round(t)).padStart(3, '0')}.png`) });
  }
  console.log(`  ok   wrote frames to ${SHOTS}`);
}

check(errors.length === 0, `no console errors${errors.length ? `: ${errors.slice(0, 3).join(' | ')}` : ''}`);

await browser.close();

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log('\nfilm smoke: all checks passed');
