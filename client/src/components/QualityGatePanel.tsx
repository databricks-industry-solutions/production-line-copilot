/**
 * What the streaming quality gate caught in the last 24 hours.
 *
 * This is drawn from `/api/ops/quality-metrics` rather than embedded from the
 * AI/BI dashboard it replaces. The dashboard is still deployed and linked above
 * for exploring — cross-filtering, drill-down, export — and this panel answers
 * the glanceable version of the same question with the same SQL.
 *
 * Anything the server could not read is named in `warnings` and rendered as an
 * em dash. A zero here would read as "the gate caught nothing", which is the
 * opposite of "we could not look".
 */
import { BarChart, DonutChart, LineChart, Skeleton } from '@databricks/appkit-ui/react';

import type {
  QcFailureRow,
  QualityMetricsPayload,
  RejectReasonRow,
  TableHealthRow,
} from '../lib/useQualityMetrics';

const CHART_HEIGHT = 220;

function num(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : v.toLocaleString();
}

/**
 * Truncated, never rounded up. At these volumes a few hundred rejected rows out
 * of a million round to 100.0%, and "100%" printed next to a non-zero reject
 * count is the tab contradicting itself. Only a gate that rejected nothing at
 * all gets to say 100%.
 */
function percent(v: number | null | undefined) {
  if (v === null || v === undefined) return '—';
  if (v >= 1) return '100%';
  return `${(Math.floor(v * 10000) / 100).toFixed(2)}%`;
}

function clockTime(iso: string | null | undefined) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString();
}

/** Hour-of-day only: 24 full timestamps will not fit on this axis. */
function hourLabel(iso: string) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${String(d.getHours()).padStart(2, '0')}:00`;
}

function Counter({
  label,
  value,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  tone?: 'neutral' | 'good' | 'bad';
}) {
  const toneClass =
    tone === 'good'
      ? 'text-emerald-600 dark:text-emerald-400'
      : tone === 'bad'
        ? 'text-red-600 dark:text-red-400'
        : 'text-foreground';
  return (
    <div className="rounded-md border border-border px-3 py-2">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`text-2xl font-bold tabular-nums ${toneClass}`}>{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
      {children}
    </div>
  );
}

function TableHealth({ rows }: { rows: TableHealthRow[] }) {
  if (rows.length === 0) return <Empty>No bronze rows landed in the last 24 hours.</Empty>;
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-xs">
        <thead className="bg-muted/50 text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium">Stream</th>
            <th className="px-2 py-1.5 text-right font-medium">Ingested</th>
            <th className="px-2 py-1.5 text-right font-medium">Rejected</th>
            <th className="px-2 py-1.5 text-right font-medium">Pass rate</th>
            <th className="px-2 py-1.5 text-right font-medium">Lag</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.table_name} className="border-t border-border">
              <td className="px-2 py-1.5">{r.label}</td>
              <td className="px-2 py-1.5 text-right tabular-nums">{num(r.bronze_rows)}</td>
              <td
                className={`px-2 py-1.5 text-right tabular-nums ${
                  r.dropped_rows > 0 ? 'text-amber-600 dark:text-amber-400' : ''
                }`}
              >
                {num(r.dropped_rows)}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums">{percent(r.pass_rate)}</td>
              <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                {r.lag_minutes === null ? '—' : `${r.lag_minutes.toFixed(1)}m`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RejectReasons({ rows }: { rows: RejectReasonRow[] }) {
  if (rows.length === 0) {
    return <Empty>Nothing rejected in the last 24 hours.</Empty>;
  }
  return (
    <div className="max-h-[220px] overflow-y-auto rounded-md border border-border">
      <table className="w-full text-xs">
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.bronze_table}:${r.rejected_reason}`} className="border-b border-border last:border-0">
              <td className="px-2 py-1.5 text-muted-foreground">{r.label}</td>
              <td className="px-2 py-1.5">{r.rejected_reason}</td>
              <td className="px-2 py-1.5 text-right font-semibold tabular-nums">
                {num(r.rejected_rows)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QcFailures({ rows }: { rows: QcFailureRow[] }) {
  if (rows.length === 0) {
    return <Empty>No QC failures in the last 24 hours.</Empty>;
  }
  return (
    <div className="max-h-[260px] overflow-auto rounded-md border border-border">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-muted/90 text-[11px] uppercase tracking-wider text-muted-foreground backdrop-blur">
          <tr>
            <th className="px-2 py-1.5 text-left font-medium">Time</th>
            <th className="px-2 py-1.5 text-left font-medium">Machine</th>
            <th className="px-2 py-1.5 text-left font-medium">Parameter</th>
            <th className="px-2 py-1.5 text-right font-medium">Value</th>
            <th className="px-2 py-1.5 text-right font-medium">Spec</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            // A QC failure carries no id, and one machine can fail the same
            // parameter twice within a second, so position is the only unique
            // part. The list is read-only and never reordered.
            // eslint-disable-next-line react/no-array-index-key
            <tr key={`${r.ts}:${r.machine_id}:${r.parameter}:${i}`} className="border-t border-border">
              <td className="px-2 py-1.5 tabular-nums text-muted-foreground">{clockTime(r.ts)}</td>
              <td className="px-2 py-1.5 font-mono">{r.machine_id}</td>
              <td className="px-2 py-1.5">{r.parameter}</td>
              <td className="px-2 py-1.5 text-right tabular-nums text-red-600 dark:text-red-400">
                {num(r.value)}
              </td>
              <td className="px-2 py-1.5 text-right tabular-nums text-muted-foreground">
                {num(r.spec_min)}–{num(r.spec_max)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function QualityGatePanel({
  data,
  loading,
  error,
}: {
  data: QualityMetricsPayload | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading && !data) {
    return (
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
        <Skeleton className="h-[220px] w-full" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
        <div className="font-medium">Quality-gate figures unavailable.</div>
        <div className="mt-1 whitespace-pre-wrap break-words font-mono opacity-80">
          {error ?? 'No data.'}
        </div>
        <div className="mt-2 text-foreground opacity-70">
          These are six read-only queries run by the app service principal. The usual cause is the
          SQL warehouse being asleep — click <strong>Reload</strong> — or the app SP having lost
          SELECT on the streaming schema.
        </div>
      </div>
    );
  }

  const s = data.summary;
  // The gate is meant to reject: a pass rate below 100% is the system working.
  // Only a materially low one is worth colouring red.
  const passTone = s?.pass_rate === null || s?.pass_rate === undefined
    ? 'neutral'
    : s.pass_rate >= 0.99
      ? 'good'
      : 'bad';

  const throughput = data.throughput.map((p) => ({
    hour: hourLabel(p.hour),
    'Bronze rows': p.row_count,
  }));
  // Rejected only. The dashboard's version plotted ingested beside it, which at
  // a pass rate this high draws the rejected series as a line of pixels against
  // a million-row bar — the number the chart exists to show, invisible. The
  // ingested counts are in the health table below, next to their pass rates.
  const rejectedPerTable = data.tables.map((t) => ({
    Stream: t.label,
    Rejected: t.dropped_rows,
  }));
  const anythingRejected = data.tables.some((t) => t.dropped_rows > 0);
  const statusMix = data.status_distribution.map((d) => ({
    Status: d.status,
    Machines: d.machines,
  }));

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]">
          Showing the last good reading — refresh failed: <span className="font-mono">{error}</span>
        </div>
      )}

      {data.warnings.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-[11px]">
          {data.warnings.map((w) => (
            <div key={w}>{w}</div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Counter label="Pass rate (24h)" value={percent(s?.pass_rate)} tone={passTone} />
        <Counter label="Rows ingested" value={num(s?.bronze_rows_24h)} />
        <Counter
          label="Rows rejected"
          value={num(s?.dropped_rows_24h)}
          tone={s?.dropped_rows_24h ? 'bad' : 'neutral'}
        />
        <Counter label="Newest row" value={clockTime(s?.latest_gold_ts)} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Hourly throughput — machine state">
          {throughput.length === 0 ? (
            <Empty>No rows in the last 24 hours.</Empty>
          ) : (
            <LineChart
              data={throughput}
              xKey="hour"
              yKey="Bronze rows"
              height={CHART_HEIGHT}
              // The axis names are drawn inside the plot box; without the room
              // the series title sits on top of the first gridline.
              options={{ grid: { left: '3%', right: '9%', top: '22%', bottom: '4%', containLabel: true } }}
              ariaLabel="Bronze rows ingested per hour over the last 24 hours"
            />
          )}
        </Section>

        <Section title="Rejected rows, by stream">
          {!anythingRejected ? (
            <Empty>
              {rejectedPerTable.length === 0
                ? 'No bronze rows landed in the last 24 hours.'
                : 'The gate rejected nothing in the last 24 hours.'}
            </Empty>
          ) : (
            <BarChart
              data={rejectedPerTable}
              xKey="Stream"
              yKey="Rejected"
              height={CHART_HEIGHT}
              options={{ grid: { left: '2%', right: '8%', top: '8%', bottom: '8%', containLabel: true } }}
              ariaLabel="Rows the quality gate rejected, per bronze table"
            />
          )}
        </Section>

        <Section title="Current status mix — last 5 minutes">
          {statusMix.length === 0 ? (
            <Empty>No machine has reported in the last 5 minutes.</Empty>
          ) : (
            <DonutChart
              data={statusMix}
              xKey="Status"
              yKey="Machines"
              height={CHART_HEIGHT}
              showLegend
              // The legend already names every slice; the leader lines then
              // collide with each other on the small ones.
              showLabels={false}
              ariaLabel="Machines by current status"
            />
          )}
        </Section>

        <Section title="Rejects by reason">
          <RejectReasons rows={data.rejects_by_reason} />
        </Section>
      </div>

      <Section title="Per-stream health">
        <TableHealth rows={data.tables} />
      </Section>

      <Section title="QC failures — measurements outside spec">
        <QcFailures rows={data.qc_failures} />
      </Section>
    </div>
  );
}
