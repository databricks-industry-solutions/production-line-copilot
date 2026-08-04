/**
 * Cross-path agreement between the live (Lakebase) plane and the analytics plane.
 * Split into a full-width verdict strip and a narrow detail rail because the Data
 * Integrity tab puts the verdict above the quality dashboard and the detail beside it.
 * Deviation is separated into explained (QC-gate rejects) and unexplained, because
 * only the latter indicates a real problem.
 */
import { deviationTone } from '../lib/reconciliationUtils';
import type { GovernedRow, ReconciliationPayload } from '../lib/useReconciliation';

const STATUS_STYLES: Record<string, string> = {
  ok: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  warn: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  error: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  unknown: 'bg-muted text-muted-foreground',
  not_enabled: 'bg-muted text-muted-foreground',
  idle: 'bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300',
  populated: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  expected_empty: 'bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300',
  empty: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  absent: 'bg-muted text-muted-foreground',
};

const STATUS_LABELS: Record<string, string> = {
  ok: 'In agreement',
  warn: 'Minor drift',
  error: 'Unexplained drift',
  unknown: 'Unknown',
  not_enabled: 'Not enabled here',
  idle: 'No traffic',
  populated: 'Populated',
  expected_empty: 'Empty (expected)',
  empty: 'Empty',
  absent: 'Not installed',
};

function Badge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        STATUS_STYLES[status] ?? STATUS_STYLES.unknown
      }`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

const TONE_CLASS: Record<ReturnType<typeof deviationTone>, string> = {
  good: 'text-emerald-600 dark:text-emerald-400',
  bad: 'text-red-600 dark:text-red-400',
  unknown: 'text-muted-foreground',
};

function num(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : v.toLocaleString();
}

function relativeTime(iso: string | null) {
  if (!iso) return '—';
  const t = Date.parse(iso.replace(' ', 'T'));
  if (Number.isNaN(t)) return iso;
  const secs = Math.round((Date.now() - t) / 1000);
  // Don't clamp a future timestamp to "0s ago" — that would render the stalest
  // possible data (a naive timestamp read as local time, or clock skew) as the
  // freshest possible reading.
  if (secs < -60) return 'in the future (clock skew?)';
  if (secs < 60) return `${Math.max(0, secs)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

/**
 * Headline verdict, full width above the two columns. The unexplained count is
 * the only number allowed to go red — everything else is context for it.
 */
export function ReconciliationVerdict({
  data,
  loading,
  error,
  onReload,
}: {
  data: ReconciliationPayload | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
}) {
  if (!data) {
    return (
      <div aria-live="polite">
        {loading && (
          <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">Reconciling paths…</div>
        )}
        {error && !loading && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive">
            <div className="font-medium">Reconciliation unavailable.</div>
            <div className="mt-1 break-words opacity-80">{error}</div>
          </div>
        )}
      </div>
    );
  }

  const headlineTone = deviationTone(data.overall, data.total_unexplained);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-4 rounded-lg border bg-card p-4">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Cross-path verdict
          </div>
          <div className="mt-1">
            <Badge status={data.overall} />
          </div>
        </div>
        <div className="border-l pl-4">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Unexplained rows
          </div>
          <div className={`text-2xl font-bold tabular-nums ${TONE_CLASS[headlineTone]}`}>
            {headlineTone === 'unknown' ? '—' : data.total_unexplained.toLocaleString()}
          </div>
          {headlineTone === 'unknown' && <div className="text-[10px] text-muted-foreground">not measurable</div>}
        </div>
        <div className="border-l pl-4">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">DQX flagged</div>
          <div className="text-2xl font-bold tabular-nums text-foreground">{data.dqx_total.toLocaleString()}</div>
          <div className="text-[10px] text-muted-foreground">kept, not dropped</div>
        </div>
        <div className="min-w-[280px] flex-1 border-l pl-4 text-[11px] leading-relaxed text-muted-foreground">
          The producer dual-writes every tick, so the same events travel the live path (Lakebase) and the analytics path
          (Zerobus → Bronze → quality gate → Gold → governed model). This compares a settled {data.window.minutes}-minute
          window ending {Math.round(data.window.lag_guard_seconds / 60)} min ago
          {data.shift_id && (
            <>
              {' '}
              for shift <code className="font-mono">{data.shift_id}</code>
            </>
          )}
          , and separates deviation the system <em>explains</em> from deviation it cannot.
        </div>
        <button
          type="button"
          className="shrink-0 self-start rounded border border-border bg-background px-2 py-1 text-xs hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
          onClick={onReload}
          disabled={loading}
        >
          {loading ? 'Reconciling…' : 'Reload'}
        </button>
      </div>

      {error && (
        <div className="mt-2 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-[11px] text-destructive">
          Last refresh failed ({error}) — figures below are from the previous run.
        </div>
      )}

      {!data.live_path_available && (
        <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
          Live path (Lakebase) unreachable — analytics counts are shown, but agreement can&apos;t be judged.
        </div>
      )}
    </div>
  );
}

/** One stream, as two lines instead of an eight-column table row. */
function StreamRow({ entity }: { entity: ReconciliationPayload['entities'][number] }) {
  const tone = deviationTone(entity.status, entity.unexplained);
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-foreground">{entity.label}</span>
        <Badge status={entity.status} />
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span
          className="font-mono text-[11px] tabular-nums text-muted-foreground"
          title="Live (Lakebase) → Bronze → Gold"
        >
          {num(entity.lakebase_rows)} <span className="opacity-50">→</span> {num(entity.bronze_rows)}{' '}
          <span className="opacity-50">→</span> {num(entity.gold_rows)}
        </span>
        <span className={`shrink-0 text-[11px] font-semibold tabular-nums ${TONE_CLASS[tone]}`}>
          {tone === 'unknown' ? 'not verified' : `${entity.unexplained.toLocaleString()} unexplained`}
        </span>
      </div>
      {(entity.explained_rejected > 0 || entity.dqx_flagged > 0) && (
        <div className="mt-0.5 text-[10px] text-muted-foreground">
          {entity.explained_rejected.toLocaleString()} rejected by the gate (explained)
          {entity.dqx_flagged > 0 && <> · {entity.dqx_flagged.toLocaleString()} DQX flagged</>}
        </div>
      )}
    </li>
  );
}

function GovernedCard({ row }: { row: GovernedRow }) {
  return (
    <li className="px-3 py-2.5">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-foreground">{row.label}</span>
        <Badge status={row.status} />
      </div>
      <div className="mt-1 flex items-baseline justify-between gap-2">
        <span className="truncate font-mono text-[10px] text-muted-foreground">
          {row.schema}.{row.table}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          <span className="font-semibold tabular-nums text-foreground">{num(row.rows)}</span>{' '}
          {row.kind === 'upsert' ? 'per entity' : 'rows'} · {relativeTime(row.last_ts)}
        </span>
      </div>
    </li>
  );
}

/**
 * The detail, sized for the ~1/3 column beside the dashboard. The long-form
 * explanations live in <details> so the rail stays scannable but the reasoning
 * is still one click away.
 */
export function ReconciliationRail({ data }: { data: ReconciliationPayload | null }) {
  if (!data) return null;
  const oee = data.oee_check;
  const governedOwned = data.governed_owned !== false;

  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Path agreement
        </h3>
        <ul className="divide-y rounded-lg border bg-card">
          {data.entities.map((e) => (
            <StreamRow key={e.key} entity={e} />
          ))}
        </ul>
        <details className="group mt-2">
          <summary className="cursor-pointer list-none text-[11px] text-muted-foreground underline-offset-2 hover:underline">
            How to read this
          </summary>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
            Live and Bronze are both written by the producer, so in a settled window they must match exactly. Gold is
            Bronze minus whatever the quality gate rejected — so a reject is the system working as designed, not drift.
            Anything left over is unexplained. Only <code>machine_state</code>, <code>line_kpi</code>,{' '}
            <code>inventory</code> and <code>events</code> are dual-written; telemetry and quality checks are
            analytics-only and are excluded rather than shown as false gaps. Counts are scoped to this environment&apos;s
            shift, since dev and prod share one Lakebase instance and separate themselves by shift alone.
          </p>
        </details>
      </section>

      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Value spot-check — OEE
        </h3>
        <div className="rounded-lg border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-baseline gap-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Live</div>
                <div className="text-base font-semibold tabular-nums">{oee.live === null ? '—' : `${oee.live}%`}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Governed</div>
                <div className="text-base font-semibold tabular-nums">
                  {oee.governed === null ? '—' : `${oee.governed}%`}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Δ</div>
                <div className="text-base font-semibold tabular-nums">{oee.delta === null ? '—' : oee.delta}</div>
              </div>
            </div>
            <Badge status={oee.status} />
          </div>
          <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
            {governedOwned
              ? (oee.note ??
                'Matching row counts prove nothing about correctness — this compares the actual value across both paths.')
              : 'Streaming silver is off in this environment, so there is no governed value of its own to compare against.'}
          </p>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Governed industry model
        </h3>
        {governedOwned ? (
          <>
            <ul className="divide-y rounded-lg border bg-card">
              {data.governed.map((g) => (
                <GovernedCard key={g.key} row={g} />
              ))}
            </ul>
            <details className="mt-2">
              <summary className="cursor-pointer list-none text-[11px] text-muted-foreground underline-offset-2 hover:underline">
                Why some of these are empty
              </summary>
              <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
                Upsert targets hold one row per entity (a line, a material), so their row count is deliberately not
                compared against event counts. Incident-driven tables are empty at steady state and fill only while an
                incident is active — &quot;Empty (expected)&quot; is the correct reading, not a failure.
              </p>
            </details>
          </>
        ) : (
          <div className="rounded-lg border bg-card p-3 text-[11px] leading-relaxed text-muted-foreground">
            Not populated by this environment. The governed tables are catalog-level and carry no environment suffix, so
            a catalog shared with another deployment would show <em>its</em> rows here — they are withheld rather than
            presented as this pipeline&apos;s output. Enable <code className="font-mono">mvm_silver_enabled</code> to
            populate and reconcile them here.
          </div>
        )}
      </section>

      {data.warnings.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-[11px]">
          <div className="mb-1 font-medium">Partial results</div>
          <ul className="list-inside list-disc space-y-0.5 opacity-80">
            {/* Index key: warnings are plain strings that can legitimately
                repeat, and the list is rebuilt wholesale per payload rather
                than reordered, so position is the only stable identity. */}
            {data.warnings.map((w, i) => (
              // eslint-disable-next-line react/no-array-index-key
              <li key={`${i}-${w}`}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
