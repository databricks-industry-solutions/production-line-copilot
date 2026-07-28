/**
 * "Ask the line" data panel — a first-class ad-hoc analytics entry point.
 *
 * Unlike ChatPanel (which runs a specialist LLM over the live snapshot), this
 * posts a free-form analytical question straight to Databricks Genie via
 * `/api/ask-line` and renders the grounded answer WITH provenance: the
 * generated SQL and the tables it touched. Collapsed by default so it never
 * competes with the chat for space until the operator opens it.
 */
import { useCallback, useState } from 'react';
import Markdown from 'react-markdown';
import { cn } from '../lib/utils';

interface AskLineResult {
  answer?: string;
  sql?: string | null;
  tables?: string[];
  data?: Array<Record<string, unknown>>;
  error?: string;
}

const SUGGESTIONS = [
  'What is the OEE trend over the last 5 shifts?',
  'Which machine has the most downtime this shift?',
  'Compare quality reject rates across machines this shift',
];

// eslint-disable-next-line react-refresh/only-export-components -- test compatibility re-export
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

export default function AskLinePanel() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskLineResult | null>(null);
  const [showSql, setShowSql] = useState(false);

  const ask = useCallback(async (q: string) => {
    const trimmed = q.trim();
    if (trimmed.length < 5 || loading) return;
    setLoading(true);
    setResult(null);
    setShowSql(false);
    try {
      const response = await fetch('/api/ask-line', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmed }),
      });
      const data = (await response.json()) as AskLineResult;
      setResult(data);
    } catch (err) {
      setResult({ error: err instanceof Error ? err.message : 'Request failed' });
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const dataColumns = result?.data && result.data.length > 0 ? Object.keys(result.data[0]) : [];

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-2 text-left"
      >
        <span className="flex items-center gap-2">
          <span className="text-sm font-semibold">Ask the line (data)</span>
          <span className="text-xs text-muted-foreground">Genie-backed analytics with SQL provenance</span>
        </span>
        <span className="text-xs text-muted-foreground">{open ? 'Hide' : 'Open'}</span>
      </button>

      {open && (
        <div className="border-t border-border px-4 py-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void ask(question);
            }}
            className="flex gap-2"
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. What is the OEE trend over the last 5 shifts?"
              className="flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:ring-1 focus:ring-primary"
            />
            <button
              type="submit"
              disabled={loading || question.trim().length < 5}
              className={cn(
                'rounded-md px-3 py-1.5 text-sm font-medium',
                loading || question.trim().length < 5
                  ? 'bg-muted text-muted-foreground'
                  : 'bg-primary text-primary-foreground',
              )}
            >
              {loading ? 'Asking…' : 'Ask'}
            </button>
          </form>

          <div className="mt-2 flex flex-wrap gap-1.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setQuestion(s);
                  void ask(s);
                }}
                className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
              >
                {s}
              </button>
            ))}
          </div>

          {result?.error && (
            <p className="mt-3 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">{result.error}</p>
          )}

          {result && !result.error && (
            <div className="mt-3 space-y-3">
              {result.answer && (
                <div className="prose prose-sm dark:prose-invert max-w-none text-sm [&_p]:my-1">
                  <Markdown>{result.answer}</Markdown>
                </div>
              )}

              {result.tables && result.tables.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Tables used
                  </span>
                  {result.tables.map((t) => (
                    <span
                      key={t}
                      className="rounded-full bg-blue-500/15 px-2 py-0.5 text-[11px] font-mono text-blue-700 dark:text-blue-300"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}

              {result.sql && (
                <details open={showSql}>
                  <summary
                    className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted-foreground select-none"
                    onClick={() => setShowSql((v) => !v)}
                  >
                    Generated SQL
                  </summary>
                  <pre className="mt-1.5 overflow-x-auto rounded-md bg-muted/60 p-2 text-[11px] leading-snug">
                    <code>{result.sql}</code>
                  </pre>
                </details>
              )}

              {dataColumns.length > 0 && (
                <div className="overflow-x-auto rounded-md border border-border">
                  <table className="w-full text-[11px]">
                    <thead className="bg-muted/60">
                      <tr>
                        {dataColumns.map((c) => (
                          <th key={c} className="px-2 py-1 text-left font-semibold">
                            {c}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.data!.slice(0, 20).map((row) => (
                        <tr
                          key={dataColumns.map((c) => formatCell(row[c])).join('|')}
                          className="border-t border-border/50"
                        >
                          {dataColumns.map((c) => (
                            <td key={c} className="px-2 py-1 tabular-nums">
                              {formatCell(row[c])}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
