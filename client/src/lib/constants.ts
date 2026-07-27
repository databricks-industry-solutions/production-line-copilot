/**
 * Central UI constants for status styling and the production-line node layout.
 * Used by `LineSchematic`, `KPIStrip`, and `App` when rendering machine state maps.
 * Coordinates are fixed to preserve visual lane ordering expected by operators and screenshots.
 */
export const STATUS_COLORS = {
  running: '#22c55e',
  stopped: '#ef4444',
  degraded: '#eab308',
  starved: '#9ca3af',
  idle: '#3b82f6',
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
