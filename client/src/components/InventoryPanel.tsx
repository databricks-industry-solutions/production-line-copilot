/**
 * Displays material health, burn-down timing, and low-stock alerts for line operations.
 * Used on the main tower tab in `App` as the inventory counterpart to machine KPIs.
 * Uses fixed-width table columns to prevent per-tick layout jitter from changing quantities/deltas.
 */
import { useEffect, useMemo, useState } from 'react';
import { Skeleton } from '@databricks/appkit-ui/react';
import { cn, toNumber } from '../lib/utils';
import DataSourceBadge from './DataSourceBadge';

export interface InventoryItem {
  material_id: string;
  on_hand_qty: number;
  safety_stock: number;
  reorder_point: number;
  status: string;
  consumption_rate_per_hour?: number;
  feeds_machine_id?: string;
}

export interface ConsumptionRate {
  material_id: string;
  total_consumed: number;
  avg_rate_per_hour: number;
}

interface Props {
  items: InventoryItem[];
  loading?: boolean;
  consumptionRates?: ConsumptionRate[];
  elapsedHours?: number;
  remainingHours?: number;
}

const INVENTORY_SOURCE_DETAILS: Record<string, string> = {
  'SAP MM': 'Material master, reorder points, and safety stock thresholds.',
  WMS: 'On-hand stock snapshots and warehouse movement updates.',
};

function getStatusStyle(status: string) {
  const s = status?.toUpperCase() ?? '';
  if (s === 'CRITICAL') return 'bg-red-500/20 text-red-700 dark:text-red-400';
  if (s === 'LOW') return 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-400';
  return 'bg-green-500/20 text-green-700 dark:text-green-400';
}

// The simulator places a purchase order the moment stock drops to the reorder
// point, and refills it after a short delivery lead time. So any material at or
// below its reorder point (i.e. status !== OK) has a replenishment in flight —
// we surface that so a LOW/CRITICAL reading reads as "reloading", not "stuck".
function isReplenishing(item: InventoryItem): boolean {
  return (item.status?.toUpperCase() ?? 'OK') !== 'OK';
}

function formatMaterialName(id: string): string {
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatQty(v: number): string {
  return v.toLocaleString(undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

function hoursUntilEmpty(onHand: number, ratePerShift: number, elapsed: number, shiftHours: number): number | null {
  if (!ratePerShift || ratePerShift <= 0 || shiftHours <= 0) return null;
  const ratePerHour = ratePerShift / shiftHours;
  if (ratePerHour <= 0) return null;
  const consumed = ratePerHour * elapsed;
  const remaining = onHand - consumed;
  if (remaining <= 0) return 0;
  return remaining / ratePerHour;
}

function formatHoursLeft(hrs: number | null): string {
  if (hrs === null) return '—';
  if (hrs <= 0) return 'Depleted';
  if (hrs < 1) return `${Math.round(hrs * 60)}m left`;
  return `~${hrs.toFixed(1)}h left`;
}

function hoursLeftColor(hrs: number | null): string {
  if (hrs === null) return '';
  if (hrs <= 0) return 'text-red-600 dark:text-red-400 font-semibold';
  if (hrs < 2) return 'text-red-600 dark:text-red-400';
  if (hrs < 4) return 'text-yellow-600 dark:text-yellow-400';
  return 'text-muted-foreground';
}

export default function InventoryPanel({
  items,
  loading = false,
  consumptionRates = [],
  elapsedHours = 0,
  remainingHours = 8,
}: Props) {
  const rateMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of consumptionRates) m.set(String(r.material_id), toNumber(r.total_consumed));
    return m;
  }, [consumptionRates]);

  const shiftHours = elapsedHours + remainingHours;

  const [expanded, setExpanded] = useState(true);
  const [prevOnHandById, setPrevOnHandById] = useState<Map<string, number>>(() => new Map());

  const deltaMap = useMemo(() => {
    const deltas = new Map<string, number>();
    for (const item of items) {
      const onHand = toNumber(item.on_hand_qty);
      const prevOnHand = prevOnHandById.get(item.material_id);
      deltas.set(item.material_id, prevOnHand == null ? 0 : onHand - prevOnHand);
    }
    return deltas;
  }, [items, prevOnHandById]);

  useEffect(() => {
    const next = new Map<string, number>();
    for (const item of items) {
      next.set(item.material_id, toNumber(item.on_hand_qty));
    }
    const timer = window.setTimeout(() => {
      setPrevOnHandById(next);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [items]);

  const alerts = useMemo(() => items.filter((i) => i.status !== 'OK'), [items]);

  if (loading) {
    return (
      <div className="rounded-lg border border-border bg-card p-3">
        <Skeleton className="h-4 w-32 mb-2" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-3 py-2 border-b border-border hover:bg-muted/30 transition-colors"
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold">Inventory</h3>
          <span className="rounded-full bg-muted px-1.5 py-px text-[11px] font-medium">{items.length} materials</span>
          <DataSourceBadge sources={['SAP MM', 'WMS']} details={INVENTORY_SOURCE_DETAILS} />
          {alerts.length > 0 && (
            <span className="rounded-full bg-yellow-500/20 px-1.5 py-px text-[11px] font-semibold text-yellow-700 dark:text-yellow-400">
              {alerts.length} alert{alerts.length > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <span className="text-muted-foreground text-[11px]">{expanded ? '\u25BC' : '\u25B6'}</span>
      </button>

      {/* Always show alerts inline, even when collapsed */}
      {!expanded && alerts.length > 0 && (
        <div className="px-3 py-1.5 space-y-1 border-b border-border">
          {alerts.map((a) => (
            <div key={a.material_id} className="flex items-center gap-2 text-[11px]">
              <span className={cn('rounded-full px-1.5 py-px text-[11px] font-semibold', getStatusStyle(a.status))}>
                {a.status}
              </span>
              <span className="font-medium">{formatMaterialName(a.material_id)}</span>
              <span className="text-muted-foreground">
                {formatQty(toNumber(a.on_hand_qty))} on hand (safety: {toNumber(a.safety_stock).toLocaleString()})
              </span>
              {isReplenishing(a) && (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-sky-500/15 px-1.5 py-px text-[10px] font-medium text-sky-700 dark:text-sky-300"
                  title="Reorder placed — delivery inbound. Stock refills after a short lead time."
                >
                  <span className="animate-pulse" aria-hidden>{'\u21BB'}</span> Restocking
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {expanded && (
        <div className="overflow-x-auto">
          {/*
            Fixed table layout with explicit column widths so the table never
            re-flows when values change width across ticks. Without this the
            ``On Hand`` column would visibly shift on every SSE refresh as
            digit counts and the optional ``(±x.y)`` delta tag changed —
            making the whole panel feel jittery. Per-column widths sum to
            ~100%; the delta tag has its own fixed-width slot.
          */}
          <table className="w-full table-fixed text-[11px]">
            <colgroup>
              <col style={{ width: '24%' }} />
              <col style={{ width: '20%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '11%' }} />
              <col style={{ width: '12%' }} />
              <col style={{ width: '10%' }} />
              <col style={{ width: '12%' }} />
            </colgroup>
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-3 py-1.5 font-medium">Material</th>
                <th className="px-3 py-1.5 font-medium text-right">On Hand</th>
                <th className="px-3 py-1.5 font-medium text-right">Safety</th>
                <th className="px-3 py-1.5 font-medium text-right">Reorder</th>
                <th className="px-3 py-1.5 font-medium text-center">Status</th>
                <th className="px-3 py-1.5 font-medium">Level</th>
                <th className="px-3 py-1.5 font-medium text-right">Time Left</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((item) => {
                const onHand = toNumber(item.on_hand_qty);
                const delta = deltaMap.get(item.material_id) ?? 0;
                const reorder = toNumber(item.reorder_point);
                const barPct = reorder > 0 ? Math.min((onHand / (reorder * 1.5)) * 100, 100) : 100;
                const isAlert = item.status !== 'OK';
                const ratePerHour = item.consumption_rate_per_hour ?? 0;
                const hrsLeft =
                  ratePerHour > 0
                    ? onHand / ratePerHour
                    : (() => {
                        const ratePerShift = rateMap.get(item.material_id) ?? 0;
                        return hoursUntilEmpty(onHand, ratePerShift, elapsedHours, shiftHours);
                      })();

                return (
                  <tr
                    key={item.material_id}
                    className={cn('hover:bg-muted/20 transition-colors', isAlert && 'bg-yellow-500/5')}
                  >
                    <td className="px-3 py-1 font-medium truncate" title={formatMaterialName(item.material_id)}>
                      {formatMaterialName(item.material_id)}
                    </td>
                    <td className="px-3 py-1 text-right tabular-nums whitespace-nowrap">
                      <span className="inline-block">{formatQty(onHand)}</span>
                      {/* Reserve a fixed-width slot for the delta tag so the
                          value column doesn't jiggle when (+x.y) appears /
                          disappears or grows from "(-0.2)" to "(+52.5)". */}
                      <span
                        className={cn(
                          'ml-1 inline-block w-[44px] text-right text-[10px]',
                          delta < 0
                            ? 'text-red-600 dark:text-red-400'
                            : delta > 0
                              ? 'text-green-600 dark:text-green-400'
                              : 'text-transparent'
                        )}
                        aria-hidden={Math.abs(delta) < 0.1 ? 'true' : undefined}
                      >
                        {Math.abs(delta) >= 0.1 ? `(${delta > 0 ? '+' : ''}${delta.toFixed(1)})` : '(\u00A0)'}
                      </span>
                    </td>
                    <td className="px-3 py-1 text-right tabular-nums text-muted-foreground">
                      {toNumber(item.safety_stock).toLocaleString()}
                    </td>
                    <td className="px-3 py-1 text-right tabular-nums text-muted-foreground">
                      {reorder.toLocaleString()}
                    </td>
                    <td className="px-3 py-1 text-center">
                      <span
                        className={cn(
                          'inline-block rounded-full px-1.5 py-px text-[11px] font-semibold',
                          getStatusStyle(item.status)
                        )}
                      >
                        {item.status}
                      </span>
                      {isReplenishing(item) && (
                        <span
                          className="ml-1 inline-block align-middle text-sky-500 animate-pulse"
                          title="Reorder placed — delivery inbound. Stock refills after a short lead time."
                          aria-label="Restocking — delivery inbound"
                        >
                          {'\u21BB'}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-1">
                      <div className="h-1.5 w-16 rounded-full bg-muted overflow-hidden">
                        <div
                          className={cn(
                            'h-full rounded-full transition-all',
                            item.status === 'CRITICAL'
                              ? 'bg-red-500'
                              : item.status === 'LOW'
                                ? 'bg-yellow-500'
                                : 'bg-green-500'
                          )}
                          style={{ width: `${barPct}%` }}
                        />
                      </div>
                    </td>
                    <td
                      className={cn(
                        'px-3 py-1 text-right tabular-nums text-[11px] whitespace-nowrap',
                        hoursLeftColor(hrsLeft)
                      )}
                    >
                      {formatHoursLeft(hrsLeft)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="px-3 py-1.5 text-[10px] text-muted-foreground border-t border-border">
            <span className="text-sky-500">{'\u21BB'}</span> Low/critical materials auto-reorder; stock refills after a short delivery lead time.
          </p>
        </div>
      )}
    </div>
  );
}
