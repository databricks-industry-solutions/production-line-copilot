/**
 * Defines machine registry metadata and fallback sensor derivations for the UI.
 * Used by `App`, `LineSchematic`, and `MachineDetailCard` for labels, neighbors, and inferred readings.
 * Fallback formulas intentionally mirror server-side sim logic so demo tiles stay numerically consistent.
 */

export type MachineType =
  | 'depalletizer'
  | 'conveyor'
  | 'uncaser'
  | 'washer'
  | 'inspector'
  | 'filler'
  | 'capper'
  | 'pasteurizer'
  | 'labeller'
  | 'coder'
  | 'packer'
  | 'shrink'
  | 'sealer'
  | 'checkweigher'
  | 'palletizer'
  | 'stretch';

export interface MachineMeta {
  id: string;
  displayName: string;
  type: MachineType;
  /** Process order on the line (1 = line start) */
  linePosition: number;
  /** Nominal design rate when applicable */
  nominalRate: number | null;
  /** Unit for nominalRate / throughput (BPM = bottles/min, etc.) */
  rateUnit: string;
  /** One-line operator-facing role */
  role: string;
  primary1: { label: string; unit: string } | null;
  primary2: { label: string; unit: string } | null;
  material: { label: string; unit: string } | null;
}

/** Flow order for upstream / downstream hints */
export const MACHINE_FLOW_ORDER: string[] = [
  'DEPALLETIZER',
  'CONVEYOR_1',
  'UNCASER',
  'BOTTLE_WASHER',
  'EMPTY_INSPECTOR',
  'CONVEYOR_2',
  'FILLER',
  'CAPPER',
  'PASTEURIZER',
  'LABELLER',
  'CODER',
  'CONVEYOR_3',
  'CASE_PACKER',
  'SHRINK_WRAPPER',
  'CASE_SEALER',
  'CHECKWEIGHER',
  'CONVEYOR_4',
  'PALLETIZER',
  'STRETCH_WRAPPER',
];

export const MACHINE_META: Record<string, MachineMeta> = {
  DEPALLETIZER: {
    id: 'DEPALLETIZER',
    displayName: 'Depalletizer',
    type: 'depalletizer',
    linePosition: 1,
    nominalRate: null,
    rateUnit: 'layers/min',
    role: 'Removes bulk layers; feeds empty bottles to the line.',
    primary1: { label: 'Cycle time (est.)', unit: 's' },
    primary2: null,
    material: null,
  },
  CONVEYOR_1: {
    id: 'CONVEYOR_1',
    displayName: 'Conveyor 1',
    type: 'conveyor',
    linePosition: 2,
    nominalRate: 25,
    rateUnit: 'm/min belt',
    role: 'Buffering and transport between depalletizer and uncaser.',
    primary1: { label: 'Belt speed', unit: 'm/min' },
    primary2: { label: 'Accumulation pressure', unit: '%' },
    material: null,
  },
  UNCASER: {
    id: 'UNCASER',
    displayName: 'Uncaser',
    type: 'uncaser',
    linePosition: 3,
    nominalRate: null,
    rateUnit: 'bottles/min',
    role: 'Extracts bottles from crates; sync with washer infeed.',
    primary1: null,
    primary2: null,
    material: null,
  },
  BOTTLE_WASHER: {
    id: 'BOTTLE_WASHER',
    displayName: 'Bottle washer',
    type: 'washer',
    linePosition: 4,
    nominalRate: 25,
    rateUnit: 'm/min tunnel',
    role: 'Caustic wash, rinse, and inspection prep.',
    primary1: { label: 'Caustic temperature', unit: '°C' },
    primary2: { label: 'Tunnel speed', unit: 'm/min' },
    material: { label: 'Caustic bath level', unit: '%' },
  },
  EMPTY_INSPECTOR: {
    id: 'EMPTY_INSPECTOR',
    displayName: 'Empty bottle inspector',
    type: 'inspector',
    linePosition: 5,
    nominalRate: null,
    rateUnit: 'bottles/min',
    role: 'Vision reject for cracks, chips, foreign material.',
    primary1: { label: 'Vision NOK rate', unit: 'count/min' },
    primary2: { label: 'Reject count (window)', unit: 'EA' },
    material: null,
  },
  CONVEYOR_2: {
    id: 'CONVEYOR_2',
    displayName: 'Conveyor 2',
    type: 'conveyor',
    linePosition: 6,
    nominalRate: 25,
    rateUnit: 'm/min belt',
    role: 'Infeed buffering to filler carousel.',
    primary1: { label: 'Belt speed', unit: 'm/min' },
    primary2: { label: 'Accumulation pressure', unit: '%' },
    material: null,
  },
  FILLER: {
    id: 'FILLER',
    displayName: 'Filler',
    type: 'filler',
    linePosition: 7,
    nominalRate: 420,
    rateUnit: 'BPM',
    role: 'Volume fill; primary line pacemaker with bowl control.',
    primary1: { label: 'Avg fill volume', unit: 'ml' },
    primary2: { label: 'Fill low alarms (window)', unit: 'EA' },
    material: { label: 'Bowl / product level', unit: '%' },
  },
  CAPPER: {
    id: 'CAPPER',
    displayName: 'Capper',
    type: 'capper',
    linePosition: 8,
    nominalRate: 420,
    rateUnit: 'BPM',
    role: 'Applies crowns / screw caps; torque-controlled.',
    primary1: { label: 'Avg application torque', unit: 'Nm' },
    primary2: null,
    material: { label: 'Cap hopper level', unit: '%' },
  },
  PASTEURIZER: {
    id: 'PASTEURIZER',
    displayName: 'Pasteurizer',
    type: 'pasteurizer',
    linePosition: 9,
    nominalRate: null,
    rateUnit: 'bottles/min',
    role: 'PU delivery; zone temperature holds.',
    primary1: { label: 'Zone 2 temperature', unit: '°C' },
    primary2: { label: 'PU actual', unit: 'PU' },
    material: null,
  },
  LABELLER: {
    id: 'LABELLER',
    displayName: 'Labeller',
    type: 'labeller',
    linePosition: 10,
    nominalRate: 420,
    rateUnit: 'BPM',
    role: 'Label apply + vision; glue temperature control.',
    primary1: { label: 'Vision NOK rate', unit: 'count/min' },
    primary2: { label: 'Glue temperature', unit: '°C' },
    material: { label: 'Label roll remaining', unit: '%' },
  },
  CODER: {
    id: 'CODER',
    displayName: 'Coder',
    type: 'coder',
    linePosition: 11,
    nominalRate: null,
    rateUnit: 'bottles/min',
    role: 'Date / lot inkjet or laser.',
    primary1: null,
    primary2: null,
    material: { label: 'Ink level', unit: '%' },
  },
  CONVEYOR_3: {
    id: 'CONVEYOR_3',
    displayName: 'Conveyor 3',
    type: 'conveyor',
    linePosition: 12,
    nominalRate: 25,
    rateUnit: 'm/min belt',
    role: 'Transport to case packer magazine.',
    primary1: { label: 'Belt speed', unit: 'm/min' },
    primary2: { label: 'Accumulation pressure', unit: '%' },
    material: null,
  },
  CASE_PACKER: {
    id: 'CASE_PACKER',
    displayName: 'Case packer',
    type: 'packer',
    linePosition: 13,
    nominalRate: 17.5,
    rateUnit: 'cases/min',
    role: 'Forms and loads 24-packs; reject tracking.',
    primary1: { label: 'Reject cases (window)', unit: 'EA' },
    primary2: { label: 'Packer speed', unit: 'cases/min' },
    material: { label: 'Carton magazine level', unit: '%' },
  },
  SHRINK_WRAPPER: {
    id: 'SHRINK_WRAPPER',
    displayName: 'Shrink wrapper',
    type: 'shrink',
    linePosition: 14,
    nominalRate: null,
    rateUnit: 'cases/min',
    role: 'Film bundle; film tension and seal.',
    primary1: null,
    primary2: null,
    material: { label: 'Film remaining', unit: '%' },
  },
  CASE_SEALER: {
    id: 'CASE_SEALER',
    displayName: 'Case sealer',
    type: 'sealer',
    linePosition: 15,
    nominalRate: null,
    rateUnit: 'cases/min',
    role: 'Tape / glue seal after shrink.',
    primary1: null,
    primary2: null,
    material: null,
  },
  CHECKWEIGHER: {
    id: 'CHECKWEIGHER',
    displayName: 'Checkweigher',
    type: 'checkweigher',
    linePosition: 16,
    nominalRate: null,
    rateUnit: 'cases/min',
    role: 'Gross case weight vs target; rejects under/over.',
    primary1: { label: 'Avg case weight', unit: 'g' },
    primary2: { label: 'Weight rejects (window)', unit: 'EA' },
    material: null,
  },
  CONVEYOR_4: {
    id: 'CONVEYOR_4',
    displayName: 'Conveyor 4',
    type: 'conveyor',
    linePosition: 17,
    nominalRate: 25,
    rateUnit: 'm/min belt',
    role: 'Outfeed to palletizer.',
    primary1: { label: 'Belt speed', unit: 'm/min' },
    primary2: { label: 'Accumulation pressure', unit: '%' },
    material: null,
  },
  PALLETIZER: {
    id: 'PALLETIZER',
    displayName: 'Palletizer',
    type: 'palletizer',
    linePosition: 18,
    nominalRate: null,
    rateUnit: 'cases/min',
    role: 'Layer grid; slip sheet and pallet build.',
    primary1: null,
    primary2: null,
    material: { label: 'Pallet magazine level', unit: '%' },
  },
  STRETCH_WRAPPER: {
    id: 'STRETCH_WRAPPER',
    displayName: 'Stretch wrapper',
    type: 'stretch',
    linePosition: 19,
    nominalRate: null,
    rateUnit: 'pallets/h',
    role: 'Film wrap and roping for warehouse.',
    primary1: null,
    primary2: null,
    material: null,
  },
};

export function getMachineMeta(machineId: string): MachineMeta | null {
  return MACHINE_META[machineId] ?? null;
}

export function flowNeighbors(machineId: string): { upstream: string | null; downstream: string | null } {
  const i = MACHINE_FLOW_ORDER.indexOf(machineId);
  if (i < 0) return { upstream: null, downstream: null };
  return {
    upstream: i > 0 ? MACHINE_FLOW_ORDER[i - 1] : null,
    downstream: i < MACHINE_FLOW_ORDER.length - 1 ? MACHINE_FLOW_ORDER[i + 1] : null,
  };
}

export interface LiveMachineRow {
  status: string;
  throughput_per_min: number;
  speed_pct: number | null;
  alarm_count: number;
  ts?: string;
  primary_sensor_1?: number | null;
  primary_sensor_2?: number | null;
  material_level_pct?: number | null;
}

/**
 * Derive plausible primary readings from live cascade fields when DB columns are absent.
 * Values are indicative for the control-tower UI, not calibrated lab data.
 */
export function deriveMachineReadings(
  machineId: string,
  live: LiveMachineRow
): {
  primary_sensor_1: number | null;
  primary_sensor_2: number | null;
  material_level_pct: number | null;
} {
  const meta = MACHINE_META[machineId];
  const sp = live.speed_pct != null ? Math.max(0, Math.min(100, live.speed_pct)) : 100;
  const tput = Math.max(0, live.throughput_per_min);
  const alarms = live.alarm_count ?? 0;
  const degraded = live.status === 'degraded' || live.status === 'stopped';

  if (!meta) {
    return { primary_sensor_1: null, primary_sensor_2: null, material_level_pct: null };
  }

  const nom = meta.nominalRate;

  if (meta.type === 'conveyor' && nom != null) {
    const belt = (nom * sp) / 100;
    const accum = Math.min(95, 18 + (100 - sp) * 0.55 + (degraded ? 12 : 0));
    return {
      primary_sensor_1: Math.round(belt * 10) / 10,
      primary_sensor_2: Math.round(accum * 10) / 10,
      material_level_pct: null,
    };
  }

  if (machineId === 'DEPALLETIZER') {
    const cyc = 14 / Math.max(0.35, sp / 100);
    return { primary_sensor_1: Math.round(cyc * 10) / 10, primary_sensor_2: null, material_level_pct: null };
  }

  if (machineId === 'BOTTLE_WASHER') {
    const temp = 78 + (sp - 100) * 0.04 - (degraded ? 4 : 0);
    const tunnel = (25 * sp) / 100;
    const level = Math.max(55, 88 - (100 - sp) * 0.35 - alarms * 1.2);
    return {
      primary_sensor_1: Math.round(temp * 10) / 10,
      primary_sensor_2: Math.round(tunnel * 10) / 10,
      material_level_pct: Math.round(level * 10) / 10,
    };
  }

  if (machineId === 'EMPTY_INSPECTOR') {
    const nok = degraded ? 4 + alarms * 0.8 : 0.8 + (100 - sp) * 0.03;
    const rej = degraded ? 2 + alarms : 0.2;
    return {
      primary_sensor_1: Math.round(nok * 10) / 10,
      primary_sensor_2: Math.round(rej * 10) / 10,
      material_level_pct: null,
    };
  }

  if (machineId === 'FILLER') {
    const base = degraded ? 322 : 329.5 + (sp - 85) * 0.02;
    const fill = Math.min(340, Math.max(318, base - alarms * 0.4));
    const lows = alarms;
    const bowl = Math.max(25, 72 + (sp / 100) * 22 - (degraded ? 18 : 0));
    return {
      primary_sensor_1: Math.round(fill * 10) / 10,
      primary_sensor_2: lows,
      material_level_pct: Math.round(bowl * 10) / 10,
    };
  }

  if (machineId === 'CAPPER') {
    const torque = Math.min(2.8, Math.max(1.2, 2.0 * (sp / 100) - (degraded ? 0.35 : 0)));
    const hop = Math.max(20, 82 - (100 - sp) * 0.25 - alarms);
    return {
      primary_sensor_1: Math.round(torque * 100) / 100,
      primary_sensor_2: null,
      material_level_pct: Math.round(hop * 10) / 10,
    };
  }

  if (machineId === 'PASTEURIZER') {
    const z2 = 72 + (sp - 100) * 0.02 - (degraded ? 3 : 0);
    const pu = Math.min(38, Math.max(12, 26 * (sp / 100) - (degraded ? 6 : 0)));
    return {
      primary_sensor_1: Math.round(z2 * 10) / 10,
      primary_sensor_2: Math.round(pu * 10) / 10,
      material_level_pct: null,
    };
  }

  if (machineId === 'LABELLER') {
    const nok = degraded ? 5 + alarms : 1.2 + (100 - sp) * 0.04;
    const glue = 145 + (sp - 100) * 0.05 - (degraded ? 8 : 0);
    const roll = Math.max(18, 85 - (100 - sp) * 0.2 - alarms * 1.5);
    return {
      primary_sensor_1: Math.round(nok * 10) / 10,
      primary_sensor_2: Math.round(glue * 10) / 10,
      material_level_pct: Math.round(roll * 10) / 10,
    };
  }

  if (machineId === 'CODER') {
    const ink = Math.max(15, 72 - alarms * 2.5 - (degraded ? 12 : 0));
    return { primary_sensor_1: null, primary_sensor_2: null, material_level_pct: Math.round(ink * 10) / 10 };
  }

  if (machineId === 'CASE_PACKER' && nom != null) {
    const spd = (nom * sp) / 100;
    const rej = alarms + (degraded ? 3 : 0);
    const mag = Math.max(22, 78 - (100 - sp) * 0.3 - alarms);
    return {
      primary_sensor_1: rej,
      primary_sensor_2: Math.round(spd * 100) / 100,
      material_level_pct: Math.round(mag * 10) / 10,
    };
  }

  if (machineId === 'SHRINK_WRAPPER') {
    const film = Math.max(20, 82 - (100 - sp) * 0.25 - alarms);
    return { primary_sensor_1: null, primary_sensor_2: null, material_level_pct: Math.round(film * 10) / 10 };
  }

  if (machineId === 'CHECKWEIGHER') {
    const w = 7920 + (sp - 100) * 4 - alarms * 12 - (degraded ? 40 : 0);
    const rej = alarms + (degraded ? 2 : 0);
    return { primary_sensor_1: Math.round(w), primary_sensor_2: rej, material_level_pct: null };
  }

  if (machineId === 'PALLETIZER') {
    const mag = Math.max(25, 80 - (100 - sp) * 0.22 - alarms * 1.2);
    return { primary_sensor_1: null, primary_sensor_2: null, material_level_pct: Math.round(mag * 10) / 10 };
  }

  if (nom != null && (meta.type === 'filler' || meta.type === 'capper' || meta.type === 'labeller')) {
    const eff = (nom * sp) / 100;
    return { primary_sensor_1: Math.round(eff * 10) / 10, primary_sensor_2: null, material_level_pct: null };
  }

  if (nom != null) {
    return {
      primary_sensor_1: Math.round(((nom * sp) / 100) * 10) / 10,
      primary_sensor_2: null,
      material_level_pct: null,
    };
  }

  return {
    primary_sensor_1: tput > 0 ? Math.round(tput * 10) / 10 : null,
    primary_sensor_2: null,
    material_level_pct: null,
  };
}
