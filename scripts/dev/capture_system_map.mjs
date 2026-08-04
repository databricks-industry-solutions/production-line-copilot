/**
 * Regenerates the System Map images used in README.md.
 *
 *   node scripts/dev/capture_system_map.mjs
 *
 * Starts the preview harness (client/vite.preview.config.ts), which renders the
 * real `SystemMap` component against fixtures captured off a deployment, and
 * screenshots it in both colour schemes — the app follows
 * `prefers-color-scheme`, and GitHub serves whichever matches the reader.
 *
 * Run this whenever the map changes: a new specialist, a new governed domain, a
 * different model in a seat. `tests/scripts/test_readme_system_map.py` fails if
 * the fixtures stop matching the app's own wiring, which is the signal that the
 * picture is stale.
 *
 * To refresh the fixtures from a live workspace first:
 *
 *   APP=https://<app-host>
 *   TOKEN=$(databricks auth token --profile <profile> | jq -r .access_token)
 *   for ep in topology models; do
 *     curl -s -H "Authorization: Bearer $TOKEN" "$APP/api/ops/$ep" \
 *       | python3 -m json.tool > scripts/dev/system_map_preview/$ep.json
 *   done
 */
import { createServer } from 'vite';
import { chromium } from 'playwright';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = path.join(ROOT, 'docs/media');

const SHOTS = [
  { scheme: 'light', file: 'system_map.png' },
  { scheme: 'dark', file: 'system_map_dark.png' },
];

const server = await createServer({
  configFile: path.join(ROOT, 'client/vite.preview.config.ts'),
  server: { port: 5199, strictPort: true },
});
await server.listen();
const url = `http://localhost:5199/preview.html`;
console.log(`preview server on ${url}`);

const browser = await chromium.launch();
try {
  for (const { scheme, file } of SHOTS) {
    const page = await browser.newPage({
      colorScheme: scheme,
      // 2x so the text stays crisp when GitHub scales the image down.
      deviceScaleFactor: 2,
      viewport: { width: 1328, height: 1200 },
    });
    await page.goto(url, { waitUntil: 'networkidle' });
    // The map renders its branches only once the topology fetch resolves.
    await page.waitForSelector('text=Genie spaces wired in this deployment');
    const target = page.locator('#capture');
    await target.screenshot({ path: path.join(OUT, file) });
    console.log(`wrote docs/media/${file} (${scheme})`);
    await page.close();
  }
} finally {
  await browser.close();
  await server.close();
}
