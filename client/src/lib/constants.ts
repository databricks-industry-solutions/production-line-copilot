/**
 * Central UI constants for status styling and the production-line node layout.
 * Used by `ProcessFlowSchematic`, `KPIStrip`, and `App` when rendering machine state maps.
 * Coordinates are fixed to preserve visual lane ordering expected by operators and screenshots.
 */
/**
 * Machine states, one step darker than the mid-tone tints these started as.
 * Those were picked while the schematic was drawn on a near-black canvas, where
 * a bright fill glows; on the white page the rest of the app lives on, yellow-500
 * against white is under 2:1 and a degraded machine's rate was legible only if
 * you already knew what it said. These are the 600 weights, which hold up as
 * small text and as a 2px border without changing which colour means what.
 */
export const STATUS_COLORS = {
  running: '#16a34a',
  stopped: '#dc2626',
  degraded: '#a16207',
  starved: '#6b7280',
  idle: '#2563eb',
} as const;

export type MachineStatus = keyof typeof STATUS_COLORS;

export const STATUS_LABELS: Record<MachineStatus, string> = {
  running: 'Running',
  stopped: 'Stopped',
  degraded: 'Degraded',
  starved: 'Starved',
  idle: 'Idle',
};

export interface MachineLayout {
  machineId: string;
  label: string;
  x: number;
  y: number;
}

export const MACHINE_LAYOUT: MachineLayout[] = [
  // Top row (y=3): Depal → Capper
  { machineId: 'DEPALLETIZER', label: 'Depal', x: 1, y: 3 },
  { machineId: 'CONVEYOR_1', label: 'Conv 1', x: 2, y: 3 },
  { machineId: 'UNCASER', label: 'Uncaser', x: 3, y: 3 },
  { machineId: 'BOTTLE_WASHER', label: 'Washer', x: 4, y: 3 },
  { machineId: 'EMPTY_INSPECTOR', label: 'Inspector', x: 5, y: 3 },
  { machineId: 'CONVEYOR_2', label: 'Conv 2', x: 6, y: 3 },
  { machineId: 'FILLER', label: 'Filler', x: 7, y: 3 },
  { machineId: 'CAPPER', label: 'Capper', x: 8, y: 3 },

  // Middle row (y=2): Pasteurizer → Sealer
  { machineId: 'PASTEURIZER', label: 'Pasteur', x: 1, y: 2 },
  { machineId: 'LABELLER', label: 'Labeller', x: 2, y: 2 },
  { machineId: 'CODER', label: 'Coder', x: 3, y: 2 },
  { machineId: 'CONVEYOR_3', label: 'Conv 3', x: 4, y: 2 },
  { machineId: 'CASE_PACKER', label: 'Packer', x: 5, y: 2 },
  { machineId: 'SHRINK_WRAPPER', label: 'Shrink', x: 6, y: 2 },
  { machineId: 'CASE_SEALER', label: 'Sealer', x: 7, y: 2 },

  // Bottom row (y=1): Checkweigher → Stretch wrapper
  { machineId: 'CHECKWEIGHER', label: 'ChkWt', x: 1, y: 1 },
  { machineId: 'CONVEYOR_4', label: 'Conv 4', x: 2, y: 1 },
  { machineId: 'PALLETIZER', label: 'Palletizer', x: 3, y: 1 },
  { machineId: 'STRETCH_WRAPPER', label: 'Stretch', x: 4, y: 1 },
];

export const MACHINE_COUNT = MACHINE_LAYOUT.length;
