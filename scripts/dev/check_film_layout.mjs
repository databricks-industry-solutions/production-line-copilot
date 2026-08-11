/**
 * Screenshots the last beat of every deep-dive chapter, and fails if anything
 * on screen overlaps anything else.
 *
 *   node scripts/dev/check_film_layout.mjs [--shots DIR]
 *
 * The chapters stack their closing notes — up to four of them in the agent
 * chapter — so that a chapter with more spoken beats than panels still puts
 * something on screen for each one. That stacking is hand-placed in CSS
 * `bottom` values against panels whose height depends on how many rows they
 * were given, which is exactly the kind of arithmetic that is right when it is
 * written and wrong two edits later. This checks it by measuring the rendered
 * boxes rather than by looking.
 *
 * It samples one frame per chapter, at the point where every reveal in that
 * chapter has landed, since that is the only moment where a collision can be
 * visible.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i === -1 ? fallback : process.argv[i + 1];
};
const shotDir = arg('--shots', null);
if (shotDir) mkdirSync(shotDir, { recursive: true });

const url = pathToFileURL('docs/media/prodline_copilot_deep_dive.html').href;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

// Opening the bundle as a file:// URL means the voice-over sidecar cannot be
// fetched. That is expected here and is not what this script is looking for.
const KNOWN_FILE_SCHEME_NOISE = [/Fetch API cannot load file:/, /net::ERR_FILE_NOT_FOUND/];
const errors = [];
page.on('console', (m) => {
  const text = m.text();
  if (m.type() === 'error' && !KNOWN_FILE_SCHEME_NOISE.some((re) => re.test(text))) errors.push(text);
});
await page.goto(url);
await page.waitForFunction(() => window.__stage);

// Chapter windows come from the film itself so this cannot drift from `DD`.
const chapters = await page.evaluate(() => window.__stage.chapters ?? null);
if (!chapters) {
  console.error('the stage does not expose its chapters — cannot pick sample frames');
  process.exit(1);
}

let failures = 0;
for (const [i, c] of chapters.entries()) {
  const end = i + 1 < chapters.length ? chapters[i + 1].start : await page.evaluate(() => window.__stage.duration);
  // Two seconds before the cut: everything has arrived, nothing has left.
  const t = end - 2;
  await page.evaluate((v) => window.__stage.setTime(v), t);
  await page.waitForTimeout(120);

  const { boxes, art } = await page.evaluate(() => {
    const canvas = document.querySelector('[data-stage], .stage') ?? document.body;
    const boxes = [];
    const art = [];
    const shown = (el) => {
      const style = getComputedStyle(el);
      return style.opacity !== '0' && style.visibility !== 'hidden';
    };
    for (const el of canvas.querySelectorAll('*')) {
      if (!shown(el)) continue;
      const r = el.getBoundingClientRect();
      // A drawing large enough to be the subject of the frame, rather than an
      // icon inside a card. Chapter 1 draws the line across the lower two
      // thirds, and a note placed over it is unreadable against the bottles.
      if (el.tagName.toLowerCase() === 'svg' && r.width > 400 && r.height > 200) {
        art.push({ x: r.x, y: r.y, w: r.width, h: r.height });
        continue;
      }
      if (el.children.length > 0) continue; // leaves only — text and rules
      const text = (el.textContent || '').trim();
      if (!text) continue;
      if (r.width < 4 || r.height < 4) continue;
      boxes.push({ text: text.slice(0, 48), x: r.x, y: r.y, w: r.width, h: r.height });
    }
    return { boxes, art };
  });

  const hits = [];
  // Text over the illustration was the failure this script missed the first
  // time: it compared text against text, so three notes sitting on the machines
  // read as a clean frame.
  for (const t of boxes) {
    for (const a of art) {
      const ox = Math.min(t.x + t.w, a.x + a.w) - Math.max(t.x, a.x);
      const oy = Math.min(t.y + t.h, a.y + a.h) - Math.max(t.y, a.y);
      if (ox > 6 && oy > 6) hits.push(`${JSON.stringify(t.text)} over the illustration`);
    }
  }
  for (let a = 0; a < boxes.length; a++) {
    for (let b = a + 1; b < boxes.length; b++) {
      const p = boxes[a];
      const q = boxes[b];
      const ox = Math.min(p.x + p.w, q.x + q.w) - Math.max(p.x, q.x);
      const oy = Math.min(p.y + p.h, q.y + q.h) - Math.max(p.y, q.y);
      // A few pixels of overlap is line-height, not a collision.
      if (ox > 6 && oy > 6) hits.push(`${JSON.stringify(p.text)} over ${JSON.stringify(q.text)}`);
    }
  }

  const off = boxes.filter((b) => b.y + b.h > 1080 || b.y < 0);
  const bad = hits.length > 0 || off.length > 0;
  if (bad) failures++;
  console.log(`  ${bad ? 'FAIL' : 'ok  '} ${c.title ?? c.id} @${t}s — ${boxes.length} boxes`);
  for (const h of hits.slice(0, 4)) console.log(`         overlap: ${h}`);
  for (const o of off.slice(0, 4)) console.log(`         off-canvas: ${JSON.stringify(o.text)} at y=${Math.round(o.y)}`);

  if (shotDir) await page.screenshot({ path: `${shotDir}/${String(i + 1).padStart(2, '0')}-${c.id}.png` });
}

await browser.close();
if (errors.length) console.error(`\nconsole errors:\n  ${errors.join('\n  ')}`);
console.log(failures ? `\n${failures} chapter(s) have a layout collision` : '\nno collisions, nothing off-canvas');
process.exit(failures || errors.length ? 1 : 0);
