import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  buildProcessFlow,
  supplyAnchor,
  conveyorSupplyAnchor,
  outfeedAnchor,
  isConveyor,
  ROW_SIZES,
  STATION_W,
  STATION_H,
} from './processFlowLayout';
import { MACHINE_FLOW_ORDER } from './machineMeta';
import { MACHINE_LAYOUT } from './constants';

const flow = buildProcessFlow();

const STATIONS = MACHINE_FLOW_ORDER.filter((id) => !isConveyor(id));
const CONVEYORS = MACHINE_FLOW_ORDER.filter(isConveyor);

/** Parse "M x1 y1 L x2 y2" back into its endpoints. */
function endpoints(d: string) {
  const n = d.match(/-?\d+(\.\d+)?/g)?.map(Number) ?? [];
  expect(n).toHaveLength(4);
  return { x1: n[0], y1: n[1], x2: n[2], y2: n[3] };
}

describe('the line the geometry describes', () => {
  it('draws every machine that product stops at, once, in process order', () => {
    expect(flow.nodes.map((n) => n.machineId)).toEqual(STATIONS);
  });

  it('draws the conveyors as the links between them instead of as stations', () => {
    expect(CONVEYORS.length).toBeGreaterThan(0);
    for (const conveyor of CONVEYORS) {
      expect(flow.byId[conveyor], `${conveyor} should not be a station`).toBeUndefined();
      expect(flow.linkOfConveyor[conveyor], `${conveyor} should be a link`).toBeDefined();
    }
    expect(flow.links.filter((l) => l.conveyorId).length).toBe(CONVEYORS.length);
  });

  it('loses no machine in the translation', () => {
    const drawn = [...flow.nodes.map((n) => n.machineId), ...Object.keys(flow.linkOfConveyor)];
    expect(new Set(drawn)).toEqual(new Set(MACHINE_FLOW_ORDER));
  });

  it('keeps registry line positions, so a station still knows where it is on the line', () => {
    expect(flow.nodes[0].position).toBe(1);
    expect(flow.nodes.at(-1)?.position).toBe(MACHINE_FLOW_ORDER.length);
    // Positions count the conveyors even though they are not stations, so they
    // stay comparable with MachineMeta.linePosition.
    for (const node of flow.nodes) {
      expect(node.position).toBe(MACHINE_FLOW_ORDER.indexOf(node.machineId) + 1);
    }
  });

  it('takes its row shape from MACHINE_LAYOUT rather than a second opinion', () => {
    const fromLayout = [3, 2, 1].map((y) => MACHINE_LAYOUT.filter((m) => m.y === y && !isConveyor(m.machineId)).length);
    expect(ROW_SIZES).toEqual(fromLayout);
    expect(ROW_SIZES.reduce((a, b) => a + b, 0)).toBe(STATIONS.length);
  });
});

describe('the serpentine', () => {
  it('alternates direction so the line never jumps back across the diagram', () => {
    ROW_SIZES.forEach((_, r) => {
      const xs = flow.nodes.filter((n) => n.row === r).map((n) => n.cx);
      const sorted = r % 2 === 0 ? [...xs].sort((a, b) => a - b) : [...xs].sort((a, b) => b - a);
      expect(xs).toEqual(sorted);
    });
  });

  it('turns in place: the last of a row sits directly above the first of the next', () => {
    let seen = 0;
    for (const size of ROW_SIZES.slice(0, -1)) {
      seen += size;
      const last = flow.nodes[seen - 1];
      const first = flow.nodes[seen];
      expect(first.cx).toBe(last.cx);
      expect(first.row).toBe(last.row + 1);
    }
  });

  it('never puts two stations in the same place', () => {
    const cells = flow.nodes.map((n) => `${n.cx}:${n.cy}`);
    expect(new Set(cells).size).toBe(cells.length);
  });

  it('leaves no station overlapping its neighbour', () => {
    ROW_SIZES.forEach((_, r) => {
      const row = [...flow.nodes.filter((n) => n.row === r)].sort((a, b) => a.x - b.x);
      for (let i = 0; i < row.length - 1; i += 1) {
        expect(row[i].x + STATION_W).toBeLessThanOrEqual(row[i + 1].x);
      }
    });
  });

  it('keeps every station inside the canvas', () => {
    for (const n of flow.nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.y).toBeGreaterThanOrEqual(0);
      expect(n.x + STATION_W).toBeLessThanOrEqual(flow.width);
      expect(n.y + STATION_H).toBeLessThanOrEqual(flow.height);
    }
  });
});

describe('the sections between stations', () => {
  it('joins every consecutive pair of stations exactly once', () => {
    expect(flow.links).toHaveLength(STATIONS.length - 1);
    flow.links.forEach((link, i) => {
      expect(link.from).toBe(STATIONS[i]);
      expect(link.to).toBe(STATIONS[i + 1]);
    });
  });

  it('puts each conveyor on the section it actually carries', () => {
    for (const link of flow.links) {
      if (!link.conveyorId) continue;
      const order = MACHINE_FLOW_ORDER;
      // The conveyor sits between these two machines on the real line.
      expect(order.indexOf(link.conveyorId)).toBeGreaterThan(order.indexOf(link.from));
      expect(order.indexOf(link.conveyorId)).toBeLessThan(order.indexOf(link.to));
    }
  });

  it('gives a conveyor section room to be drawn as a belt', () => {
    const belts = flow.links.filter((l) => l.conveyorId);
    const plain = flow.links.filter((l) => !l.conveyorId && l.kind === 'along-row');
    expect(belts.length).toBeGreaterThan(0);
    expect(plain.length).toBeGreaterThan(0);
    // Rollers and a label need more room than a plain hand-over.
    expect(Math.min(...belts.map((l) => l.length))).toBeGreaterThan(Math.max(...plain.map((l) => l.length)));
  });

  it('draws every path upstream to downstream, so one dash direction reads as forward', () => {
    for (const link of flow.links) {
      const { x1, y1, x2, y2 } = endpoints(link.d);
      const from = flow.byId[link.from];
      const to = flow.byId[link.to];
      const startsAtFrom = Math.abs(x1 - from.cx) <= STATION_W / 2 && Math.abs(y1 - from.cy) <= STATION_H / 2;
      const endsAtTo = Math.abs(x2 - to.cx) <= STATION_W / 2 && Math.abs(y2 - to.cy) <= STATION_H / 2;
      expect(startsAtFrom, `${link.from}->${link.to} starts at ${link.from}`).toBe(true);
      expect(endsAtTo, `${link.from}->${link.to} ends at ${link.to}`).toBe(true);
    }
  });

  it('spans the gap without being drawn underneath a station', () => {
    for (const link of flow.links.filter((l) => l.kind === 'along-row')) {
      const { x1, x2 } = endpoints(link.d);
      const from = flow.byId[link.from];
      const to = flow.byId[link.to];
      const gapLeft = Math.min(from.x + STATION_W, to.x + STATION_W);
      const gapRight = Math.max(from.x, to.x);
      expect(Math.min(x1, x2)).toBeGreaterThanOrEqual(gapLeft);
      expect(Math.max(x1, x2)).toBeLessThanOrEqual(gapRight);
    }
  });

  it('records which way it runs, so a paused diagram still reads', () => {
    for (const link of flow.links) {
      const { x1, y1, x2, y2 } = endpoints(link.d);
      if (link.kind === 'row-change') {
        expect(link.dirDeg).toBe(90);
        expect(y2).toBeGreaterThan(y1);
      } else {
        expect(link.dirDeg).toBe(x2 > x1 ? 0 : 180);
      }
    }
    // The middle row runs the other way; that is the whole point of recording it.
    expect(new Set(flow.links.map((l) => l.dirDeg))).toEqual(new Set([0, 90, 180]));
  });

  it('makes each row change a vertical drop in a single column', () => {
    const turns = flow.links.filter((l) => l.kind === 'row-change');
    expect(turns).toHaveLength(ROW_SIZES.length - 1);
    for (const link of turns) {
      const { x1, y1, x2, y2 } = endpoints(link.d);
      expect(x1).toBe(x2);
      expect(y2).toBeGreaterThan(y1);
    }
  });

  it('never hides a conveyor on a row change, where a belt could not be drawn', () => {
    for (const link of flow.links.filter((l) => l.kind === 'row-change')) {
      expect(link.conveyorId).toBeNull();
    }
  });
});

describe('supply and outfeed anchors', () => {
  it('hangs a supply chip above its station without landing on the row above', () => {
    for (const node of flow.nodes) {
      const anchor = supplyAnchor(node);
      expect(anchor.x).toBe(node.cx);
      expect(anchor.y).toBeGreaterThanOrEqual(0);
      expect(anchor.lineY2).toBe(node.y);
      expect(anchor.lineY1).toBeLessThan(anchor.lineY2);
      if (node.row > 0) {
        const rowAboveBottom = flow.nodes.find((n) => n.row === node.row - 1)!.y + STATION_H;
        expect(anchor.y).toBeGreaterThan(rowAboveBottom);
      }
    }
  });

  it('hangs one above a conveyor too, because belt lubricant feeds one', () => {
    for (const link of Object.values(flow.linkOfConveyor)) {
      const anchor = conveyorSupplyAnchor(link);
      expect(anchor.x).toBe(link.mx);
      expect(anchor.y).toBeGreaterThanOrEqual(0);
      expect(anchor.lineY1).toBeLessThan(anchor.lineY2);
      expect(anchor.lineY2).toBeLessThan(link.my);
    }
  });

  it('puts the outfeed at the far side of the last station', () => {
    const last = flow.nodes.at(-1)!;
    expect(outfeedAnchor(flow)).toEqual({ x: last.x + STATION_W, y: last.cy });
  });
});

/**
 * Folding a conveyor into a link needs a station on each side of it to be the
 * link between. Today's line always has both, so these are about the line this
 * plant might commission next, not the one it runs now.
 *
 * They matter because of how the failure would look: a machine that disappears
 * from a diagram whose entire claim is that it draws the whole line. Nothing
 * about the picture would seem wrong — it would simply be missing an asset, and
 * an operator would trust it anyway.
 */
describe('a conveyor with nothing to fold into', () => {
  /** Rebuild the layout over a hypothetical line, with new ids typed as belts. */
  async function layoutOf(order: string[], newBelts: string[]) {
    vi.resetModules();
    vi.doMock('./machineMeta', async () => {
      const actual = await vi.importActual<typeof import('./machineMeta')>('./machineMeta');
      const meta = { ...actual.MACHINE_META };
      for (const id of newBelts) meta[id] = { ...actual.MACHINE_META[CONVEYORS[0]] };
      return { ...actual, MACHINE_FLOW_ORDER: order, MACHINE_META: meta };
    });
    const { buildProcessFlow: rebuild } = await import('./processFlowLayout');
    return rebuild();
  }

  /** Every machine reachable on the diagram, however it is drawn. */
  const drawn = (built: ReturnType<typeof buildProcessFlow>) =>
    new Set([...built.nodes.map((n) => n.machineId), ...Object.keys(built.linkOfConveyor)]);

  afterEach(() => {
    vi.doUnmock('./machineMeta');
    vi.resetModules();
  });

  it('keeps its own station when it ends the line', async () => {
    const order = [...MACHINE_FLOW_ORDER, 'CONVEYOR_OUT'];
    const built = await layoutOf(order, ['CONVEYOR_OUT']);

    expect(built.byId['CONVEYOR_OUT']).toBeDefined();
    expect(drawn(built)).toEqual(new Set(order));
  });

  it('keeps its own station when it starts the line', async () => {
    const order = ['CONVEYOR_IN', ...MACHINE_FLOW_ORDER];
    const built = await layoutOf(order, ['CONVEYOR_IN']);

    expect(built.byId['CONVEYOR_IN']).toBeDefined();
    expect(drawn(built)).toEqual(new Set(order));
  });

  it('keeps the second of two in a row, rather than letting it overwrite the first', async () => {
    const at = MACHINE_FLOW_ORDER.indexOf(CONVEYORS[0]);
    const order = [...MACHINE_FLOW_ORDER];
    order.splice(at + 1, 0, 'CONVEYOR_1B');
    const built = await layoutOf(order, ['CONVEYOR_1B']);

    expect(drawn(built)).toEqual(new Set(order));
    // The first still becomes the link it always was; the second, having no
    // station upstream of it any more, is drawn in its own right.
    expect(built.linkOfConveyor[CONVEYORS[0]]).toBeDefined();
    expect(built.byId['CONVEYOR_1B']).toBeDefined();
  });
});
