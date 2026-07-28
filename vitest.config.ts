import { defineConfig } from 'vitest/config';
import path from 'node:path';

export default defineConfig({
  test: {
    passWithNoTests: true,
    globals: true,
    environment: 'node',
    exclude: ['**/node_modules/**', '**/dist/**', '**/*.spec.ts', '**/.smoke-test/**', '**/.databricks/**'],
    server: {
      // AppKit-UI 0.48 charts use extensionless ESM subpaths (echarts-for-react/esm/core)
      // that Node's resolver cannot resolve; inlining routes them through Vite's resolver.
      deps: {
        inline: [/@databricks\/appkit-ui/, /echarts-for-react/],
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './client/src'),
    },
  },
});
