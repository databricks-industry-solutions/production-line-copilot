/**
 * Regenerates the process-flow image used in the docs.
 *
 *   node scripts/dev/capture_process_flow.mjs
 *
 * Renders the real `ProcessFlowSchematic` through the preview harness
 * (client/vite.preview.config.ts) against the cascade fixture in
 * `client/src/preview.tsx` — a stopped filler, blocked upstream, starved
 * downstream — and screenshots it in both colour schemes, since the app follows
 * `prefers-color-scheme` and GitHub serves whichever matches the reader.
 *
 * The schematic canvas itself is dark in both, deliberately; what changes
 * between the two shots is the page around it.
 *
 * Animation is paused for the capture. The dashes encode rate by moving, so a
 * still frame catches them mid-cycle at whatever offset the clock happened to
 * be at, and two runs would produce two different images for the same line.
 */
import { createServer } from 'vite';
import { chromium } from 'playwright';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = path.join(ROOT, 'docs/media');

const SHOTS = [
  { scheme: 'light', file: 'process_flow.png' },
  { scheme: 'dark', file: 'process_flow_dark.png' },
];

const server = await createServer({
  configFile: path.join(ROOT, 'client/vite.preview.config.ts'),
  server: { port: 5198, strictPort: true },
});
await server.listen();
const url = 'http://localhost:5198/preview.html?view=process-flow';
console.log(`preview server on ${url}`);

const browser = await chromium.launch();
try {
  for (const { scheme, file } of SHOTS) {
    const page = await browser.newPage({
      colorScheme: scheme,
      // 2x so the small instrument text stays crisp when scaled down.
      deviceScaleFactor: 2,
      viewport: { width: 1468, height: 1100 },
      reducedMotion: 'reduce',
    });
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForSelector('text=Live line schematic');
    const target = page.locator('#capture');
    await target.screenshot({ path: path.join(OUT, file) });
    console.log(`wrote docs/media/${file} (${scheme})`);
    await page.close();
  }
} finally {
  await browser.close();
  await server.close();
}
