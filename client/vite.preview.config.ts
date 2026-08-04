/**
 * Vite config for the screenshot harness behind `client/preview.html`.
 *
 * Separate from `vite.config.ts` for two reasons the app config cannot give up:
 * it runs in `middlewareMode`, because Express owns the server in dev and mounts
 * Vite inside it, and middleware mode will not serve a standalone HTML entry.
 * It also pre-bundles `recharts`, which the preview never imports and which
 * fails to optimise when Vite runs on its own.
 *
 * The plugin below answers the two ops routes `SystemMap` fetches, from
 * fixtures captured off a real deployment. No Databricks auth, no Express, no
 * running agent server — `scripts/dev/capture_system_map.mjs` can therefore
 * regenerate the README image on any machine with a checkout.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import type { Plugin } from 'vite';
import { defineConfig } from 'vite';

const FIXTURES = path.resolve(__dirname, '../scripts/dev/system_map_preview');

const ROUTES: Record<string, string> = {
  '/api/ops/topology': 'topology.json',
  '/api/ops/models': 'models.json',
};

function opsFixtures(): Plugin {
  return {
    name: 'system-map-ops-fixtures',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const file = ROUTES[(req.url ?? '').split('?')[0]];
        if (!file) return next();
        res.setHeader('Content-Type', 'application/json');
        res.end(readFileSync(path.join(FIXTURES, file), 'utf8'));
      });
    },
  };
}

export default defineConfig({
  root: __dirname,
  plugins: [react(), tailwindcss(), opsFixtures()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
