import { afterEach, describe, expect, it, vi } from 'vitest';
import { rewriteQualifiedSchema } from './sqlRewrite';

describe('rewriteQualifiedSchema', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });
  it('rewrites the template schema to target UC catalog/schema', () => {
    const input = 'SELECT * FROM main.line_manager_ai_kit.machine_state';
    const out = rewriteQualifiedSchema(input, { ucCatalog: 'main', ucSchema: 'line_manager_ai_kit_preview' });
    expect(out).toBe('SELECT * FROM main.line_manager_ai_kit_preview.machine_state');
  });

  it('rewrites bundle placeholders to target UC catalog/schema', () => {
    const input = 'SELECT * FROM ${var.catalog}.${var.schema}.event_log';
    const out = rewriteQualifiedSchema(input, { ucCatalog: 'main', ucSchema: 'line_manager_ai_kit_preview' });
    expect(out).toBe('SELECT * FROM main.line_manager_ai_kit_preview.event_log');
  });

  it('falls back to defaults when env values look like placeholders', () => {
    const input = 'SELECT * FROM main.line_manager_ai_kit.line_kpi';
    const out = rewriteQualifiedSchema(input, { ucCatalog: '${var.catalog}', ucSchema: '${var.schema}' });
    expect(out).toBe('SELECT * FROM main.line_manager_ai_kit_preview.line_kpi');
  });

  it('rewrites SQL_TEMPLATE_CATALOG.SQL_TEMPLATE_SCHEMA when set', () => {
    vi.stubEnv('SQL_TEMPLATE_CATALOG', 'other_cat');
    vi.stubEnv('SQL_TEMPLATE_SCHEMA', 'other_sch');
    const input = 'SELECT * FROM other_cat.other_sch.machine_state';
    const out = rewriteQualifiedSchema(input, { ucCatalog: 'main', ucSchema: 'line_manager_streaming' });
    expect(out).toBe('SELECT * FROM main.line_manager_streaming.machine_state');
  });
});
