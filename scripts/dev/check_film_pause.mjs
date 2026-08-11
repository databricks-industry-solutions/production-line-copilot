/**
 * Pausing a film must silence its narration.
 *
 * The films publish their clock to localStorage and the voice player infers
 * everything else from it — there is no pause event to listen for. So "paused"
 * means "the clock has not moved for a moment", and getting that wrong is not
 * a subtle failure: with `playing` stuck true, the player keeps re-seeking a
 * still-running audio element back to a frozen offset, and the last quarter
 * second of narration loops for as long as the film sits paused.
 *
 * This drives a real browser and counts the re-seeks, because the bug lives in
 * the interaction between the rAF loop and the media element, which is exactly
 * what a static read of the file will not show you.
 *
 * Usage:
 *   node scripts/dev/check_film_pause.mjs
 *   node scripts/dev/check_film_pause.mjs --seconds 5   # sit paused for longer
 */
import { chromium } from 'playwright';
import { pathToFileURL } from 'node:url';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const argv = process.argv.slice(2);
const arg = (k, d) => (argv.includes(k) ? argv[argv.indexOf(k) + 1] : d);
const settle = Number(arg('--seconds', 2.5)) * 1000;

// Each film, and a time that lands inside one of its narration segments.
const FILMS = [
  { file: 'docs/media/prodline_copilot_film.html', at: 20, name: 'the two-minute film' },
  { file: 'docs/media/prodline_copilot_deep_dive.html', at: 130, name: 'the deep dive' },
];

const browser = await chromium.launch({
  args: ['--autoplay-policy=no-user-gesture-required', '--mute-audio'],
});

let failed = 0;
for (const film of FILMS) {
  const path = resolve(film.file);
  if (!existsSync(path)) {
    console.log(`  skip ${film.name} — ${film.file} is not in this tree`);
    continue;
  }

  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.__probe = { seeks: 0, els: [] };
    const desc = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
      get() { return desc.get.call(this); },
      set(v) { window.__probe.seeks++; desc.set.call(this, v); },
    });
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (...a) {
      if (!window.__probe.els.includes(this)) window.__probe.els.push(this);
      return play.apply(this, a);
    };
  });

  await page.goto(pathToFileURL(path).href);
  await page.waitForFunction(() => window.__stage != null, { timeout: 30_000 });
  await page.mouse.click(960, 540); // the gesture that unlocks audio

  await page.evaluate((t) => { window.__stage.setTime(t); window.__stage.setPlaying(true); }, film.at);
  await page.waitForTimeout(2500);
  const spoke = await page.evaluate(() => window.__probe.els.length > 0);

  await page.evaluate(() => window.__stage.setPlaying(false));
  await page.waitForTimeout(400); // let the pause settle before counting
  await page.evaluate(() => { window.__probe.seeks = 0; });
  await page.waitForTimeout(settle);

  const after = await page.evaluate(() => ({
    seeks: window.__probe.seeks,
    allPaused: window.__probe.els.every((e) => e.paused),
  }));
  await page.close();

  if (!spoke) {
    console.log(`  WARN ${film.name} — nothing spoke at ${film.at}s, so the pause was not exercised`);
    continue;
  }
  if (after.seeks === 0 && after.allPaused) {
    console.log(`  ok   ${film.name} — silent for ${settle / 1000}s after pause`);
  } else {
    failed++;
    console.log(
      `  FAIL ${film.name} — ${after.seeks} re-seek(s) while paused, ` +
      `audio ${after.allPaused ? 'paused' : 'still running'}: the narration is looping`,
    );
  }
}

await browser.close();
console.log(failed ? `\n${failed} film(s) keep talking while paused` : '\npause is silent in every film');
process.exit(failed ? 1 : 0);
