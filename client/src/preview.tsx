/**
 * Renders one component on its own so it can be screenshotted for the docs.
 *
 * Two views, chosen with `?view=`:
 *
 *   system-map    (default) `SystemMap`, fetching `/api/ops/topology` and
 *                 `/api/ops/models` exactly as it does in the About tab. The
 *                 preview server answers both from fixtures captured off the
 *                 deployed app (see `scripts/dev/capture_system_map.mjs`), so
 *                 the README picture shows a real deployment's wiring rather
 *                 than numbers someone typed into a diagram.
 *   process-flow  `ProcessFlowSchematic` against a fixed cascade below. This one
 *                 cannot use captured fixtures: it is a picture of a line in
 *                 motion, and a screenshot of an idle line says nothing about
 *                 what the view is for. The fixture is a stopped filler and the
 *                 damage either side of it, which is the same moment the demo
 *                 script walks an audience through.
 *   quality-gate  `QualityGatePanel`, the charts that replaced the embedded
 *                 AI/BI dashboard. Fixture rather than capture for the same
 *                 reason as the schematic: a healthy environment rejects
 *                 nothing, so a captured payload renders three empty tables and
 *                 shows none of the work. This one has the gate catching things.
 *
 * Not part of the app bundle: reachable only through `client/preview.html`,
 * which `client/vite.config.ts` never builds.
 */
import { createRoot } from 'react-dom/client';

import './index.css';
import SystemMap from './components/SystemMap';
import ProcessFlowSchematic from './components/ProcessFlowSchematic';
import QualityGatePanel from './components/QualityGatePanel';
import { MACHINE_FLOW_ORDER } from './lib/machineMeta';
import type { LiveStation } from './lib/processFlowState';
import type { QualityMetricsPayload } from './lib/useQualityMetrics';
import type { InventoryItem } from './lib/useSSELiveData';

/**
 * A line one minute after the filler stopped.
 *
 * Built by walking the flow order rather than listing 19 machines, so it stays
 * correct if the line gains a station. Upstream of the fault the buffers have
 * filled and those machines are blocked; downstream they have drained and those
 * machines are starved; the far end is still working through what was already
 * past the filler when it went down.
 */
function cascadeFixture(): Map<string, LiveStation> {
  const faultAt = MACHINE_FLOW_ORDER.indexOf('FILLER');
  const states = new Map<string, LiveStation>();

  MACHINE_FLOW_ORDER.forEach((machineId, i) => {
    const distance = i - faultAt;
    if (distance === 0) {
      states.set(machineId, { status: 'stopped', throughput_per_min: 0, speed_pct: 0, alarm_count: 2 });
    } else if (distance < 0 && distance >= -2) {
      states.set(machineId, { status: 'blocked', throughput_per_min: 0, speed_pct: 0, alarm_count: 0 });
    } else if (distance < 0) {
      states.set(machineId, { status: 'running', throughput_per_min: 418, speed_pct: 99, alarm_count: 0 });
    } else if (distance <= 5) {
      states.set(machineId, { status: 'starved', throughput_per_min: 0, speed_pct: 0, alarm_count: 0 });
    } else {
      // Past the case packer the unit is cases, not bottles.
      states.set(machineId, { status: 'degraded', throughput_per_min: 11.4, speed_pct: 65, alarm_count: 1 });
    }
  });

  return states;
}

const INVENTORY: InventoryItem[] = [
  {
    material_id: 'CAUSTIC',
    on_hand_qty: 4200,
    safety_stock: 800,
    reorder_point: 1500,
    status: 'OK',
    consumption_rate_per_hour: 180,
    feeds_machine_id: 'BOTTLE_WASHER',
  },
  {
    material_id: 'CROWNS',
    on_hand_qty: 21000,
    safety_stock: 20000,
    reorder_point: 40000,
    status: 'LOW',
    consumption_rate_per_hour: 25000,
    feeds_machine_id: 'CAPPER',
  },
  {
    material_id: 'LABEL_ROLL',
    on_hand_qty: 96000,
    safety_stock: 20000,
    reorder_point: 40000,
    status: 'OK',
    consumption_rate_per_hour: 25000,
    feeds_machine_id: 'LABELLER',
  },
  {
    material_id: 'INK',
    on_hand_qty: 22,
    safety_stock: 10,
    reorder_point: 25,
    status: 'LOW',
    consumption_rate_per_hour: 3.2,
    feeds_machine_id: 'CODER',
  },
  {
    material_id: 'CARTONS',
    on_hand_qty: 5400,
    safety_stock: 900,
    reorder_point: 1800,
    status: 'OK',
    consumption_rate_per_hour: 1050,
    feeds_machine_id: 'CASE_PACKER',
  },
  {
    material_id: 'SHRINK_FILM',
    on_hand_qty: 140,
    safety_stock: 150,
    reorder_point: 320,
    status: 'CRITICAL',
    consumption_rate_per_hour: 44,
    feeds_machine_id: 'SHRINK_WRAPPER',
  },
  {
    material_id: 'PALLETS',
    on_hand_qty: 310,
    safety_stock: 60,
    reorder_point: 120,
    status: 'OK',
    consumption_rate_per_hour: 18,
    feeds_machine_id: 'PALLETIZER',
  },
  // A conveyor consumes something too, and the real line says so: belt
  // lubricant feeds CONVEYOR_1. Kept in the fixture so the capture exercises a
  // feed landing on a belt rather than only on stations.
  {
    material_id: 'BELT_LUBE',
    on_hand_qty: 38,
    safety_stock: 10,
    reorder_point: 20,
    status: 'OK',
    consumption_rate_per_hour: 2,
    feeds_machine_id: 'CONVEYOR_1',
  },
];

const KPI = { total_throughput: 11.4, cumulative_cases: 8412, oee_to_now: 61.8 };

/**
 * A day on which the gate earned its keep.
 *
 * Row counts and the ingest/reject split are dev's real proportions; the
 * rejects and QC failures are the ones dev has not produced. `event_log`
 * carries no rows and therefore no timestamp, which is the null the table has
 * to render as an em dash rather than as a zero-minute lag.
 */
const QUALITY: QualityMetricsPayload = {
  window_hours: 24,
  summary: {
    bronze_rows_24h: 3096540,
    dropped_rows_24h: 1834,
    pass_rate: 0.999408,
    latest_gold_ts: '2026-08-17T14:48:03.559774+00:00',
  },
  tables: [
    { table_name: 'event_log', label: 'Events', bronze_rows: 0, dropped_rows: 0, pass_rate: 1, latest_ts: null, lag_minutes: null },
    { table_name: 'inventory_status', label: 'Inventory', bronze_rows: 1378016, dropped_rows: 402, pass_rate: 0.999708, latest_ts: '2026-08-17T14:48:06.257Z', lag_minutes: 0.05 },
    { table_name: 'line_kpi', label: 'Line KPI', bronze_rows: 86125, dropped_rows: 31, pass_rate: 0.99964, latest_ts: '2026-08-17T14:48:05.257Z', lag_minutes: 0.07 },
    { table_name: 'machine_state', label: 'Machine state', bronze_rows: 1632399, dropped_rows: 1401, pass_rate: 0.999142, latest_ts: '2026-08-17T14:48:03.559Z', lag_minutes: 0.09 },
  ],
  throughput: Array.from({ length: 24 }, (_, i) => ({
    hour: `2026-08-16T${String((14 + i) % 24).padStart(2, '0')}:00:00.000Z`,
    // A dip through the small hours, the way a real shift pattern shows up.
    row_count: i >= 9 && i <= 14 ? 41000 + i * 300 : 68000 + (i % 5) * 400,
  })),
  status_distribution: [
    { status: 'running', machines: 15 },
    { status: 'starved', machines: 2 },
    { status: 'blocked', machines: 1 },
    { status: 'stopped', machines: 1 },
  ],
  rejects_by_reason: [
    { bronze_table: 'machine_state', label: 'Machine state', rejected_reason: 'speed_pct outside 0-120', rejected_rows: 1288 },
    { bronze_table: 'inventory_status', label: 'Inventory', rejected_reason: 'on_hand_qty negative', rejected_rows: 402 },
    { bronze_table: 'machine_state', label: 'Machine state', rejected_reason: 'unknown machine_id', rejected_rows: 113 },
    { bronze_table: 'line_kpi', label: 'Line KPI', rejected_reason: 'ts older than the window', rejected_rows: 31 },
  ],
  qc_failures: [
    { ts: '2026-08-17T14:41:12.000Z', machine_id: 'FILLER', parameter: 'fill_volume_ml', value: 486.2, spec_min: 495, spec_max: 505, result: 'FAIL' },
    { ts: '2026-08-17T14:22:50.000Z', machine_id: 'CAPPER', parameter: 'torque_nm', value: 2.31, spec_min: 2.4, spec_max: 3.2, result: 'FAIL' },
    { ts: '2026-08-17T13:58:04.000Z', machine_id: 'PASTEURIZER', parameter: 'temp_c', value: 74.8, spec_min: 71, spec_max: 74, result: 'FAIL' },
  ],
  warnings: [],
};

const view = new URLSearchParams(window.location.search).get('view') ?? 'system-map';

createRoot(document.getElementById('root')!).render(
  // Screenshot target. The padding is the margin the capture crops to, and the
  // width matches the app's content column so the layout breaks where it breaks
  // in the app — for the schematic that is the three-of-four column it sits in
  // on Line Status, not the full page.
  <div id="capture" style={{ width: view === 'process-flow' ? 1160 : 1280, padding: 24 }}>
    {view === 'process-flow' ? (
      <ProcessFlowSchematic
        machineStates={cascadeFixture()}
        inventory={INVENTORY}
        kpi={KPI}
        selectedMachine={null}
        onMachineClick={() => {}}
      />
    ) : view === 'quality-gate' ? (
      <QualityGatePanel data={QUALITY} loading={false} error={null} />
    ) : (
      <SystemMap />
    )}
  </div>
);
