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
 *
 * Not part of the app bundle: reachable only through `client/preview.html`,
 * which `client/vite.config.ts` never builds.
 */
import { createRoot } from 'react-dom/client';

import './index.css';
import SystemMap from './components/SystemMap';
import ProcessFlowSchematic from './components/ProcessFlowSchematic';
import { MACHINE_FLOW_ORDER } from './lib/machineMeta';
import type { LiveStation } from './lib/processFlowState';
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
    ) : (
      <SystemMap />
    )}
  </div>
);
