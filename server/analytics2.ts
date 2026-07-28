import fs from 'node:fs';
import path from 'node:path';
import { rewriteQualifiedSchema } from './sqlRewrite.js';

export type AnalyticsQueryFn = (sqlText: string, parameters: Record<string, unknown>) => Promise<unknown>;

export type Analytics2Result = {
  status: number;
  body: { data: unknown[]; error?: string };
};

export function loadQuerySql(
  queryKey: string,
  cwd: string = process.cwd()
): { status: number; raw?: string; error?: string } {
  const key = String(queryKey || '').trim();
  if (!key) return { status: 400, error: 'Missing queryKey' };

  const queriesDir = path.resolve(cwd, 'config', 'queries');
  const filePath = path.join(queriesDir, `${key}.sql`);
  if (!fs.existsSync(filePath)) {
    return { status: 404, error: `Query file not found: ${key}.sql` };
  }

  const raw = fs.readFileSync(filePath, 'utf8');
  return { status: 200, raw };
}

export async function executeAnalytics2Query(opts: {
  queryKey: string;
  parameters: Record<string, unknown> | null | undefined;
  analyticsQuery: AnalyticsQueryFn;
  cwd?: string;
}): Promise<Analytics2Result> {
  const loaded = loadQuerySql(opts.queryKey, opts.cwd);
  if (loaded.status !== 200 || !loaded.raw) {
    return { status: loaded.status, body: { data: [], error: loaded.error || 'Query load failed' } };
  }

  const rewritten = rewriteQualifiedSchema(loaded.raw);
  const params = opts.parameters ?? {};

  try {
    const result = await opts.analyticsQuery(rewritten, params);
    return {
      status: 200,
      body: { data: Array.isArray(result) ? (result as unknown[]) : [] },
    };
  } catch (e: unknown) {
    const err = e as Error;
    return {
      status: 500,
      body: { data: [], error: err?.message || 'analytics2 query failed' },
    };
  }
}
