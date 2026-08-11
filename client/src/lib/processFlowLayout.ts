/**
 * Geometry for the process-flow schematic: where each machine sits and the path
 * product takes between them.
 * Used by `ProcessFlowSchematic` to place stations, draw conveyors, and anchor supply feeds.
 * Kept pure and free of React so the layout can be asserted directly in tests.
 *
 * The line has nineteen machines but only fifteen of them are drawn as stations.
 * The other four are conveyors, and a conveyor is not a place product stops — it
 * is the thing that carries product between the places where something happens
 * to it. Drawn as a box in the row it reads as another process step, which is
 * why the first version of this diagram had "Conv 1" sitting between the
 * depalletiser and the uncaser looking like a machine that does something to a
 * bottle. Here each conveyor becomes the link on either side of it, so the
 * schematic matches how the line is actually built and how anyone who works on
 * one would draw it. They keep their identity: a conveyor is still a machine
 * with a status, alarms and a detail card, it is just shaped like what it is.
 */
import { MACHINE_LAYOUT } from './constants';
import { MACHINE_FLOW_ORDER, MACHINE_META } from './machineMeta';

/** SVG user units. The whole diagram scales via viewBox, so these are ratios
 *  in disguise — only their relative sizes matter. */
export const STATION_W = 78;
export const STATION_H = 74;

/** A gap holding a conveyor has to be long enough to read as a belt with
 *  rollers and carry its own label; a direct transfer only needs to show
 *  that two machines are joined and which way. */
const CONVEYOR_GAP = 104;
const PLAIN_GAP = 44;

/** Half the gap between a belt's two rails. Shared so a feed stub lands on it. */
export const BELT_HALF_HEIGHT = 7;

/** Room for the infeed and outfeed arrows and their labels, which sit outside
 *  the first and last stations rather than inside the grid. */
const MARGIN_LEFT = 92;
const MARGIN_RIGHT = 96;

/**
 * The vertical rhythm is set by the two things that live outside a station: a
 * supply chip hangs `CHIP_CLEARANCE` above the station it feeds (see
 * `supplyAnchor`, 20 of chip and 26 of connector) and two lines of instrument
 * text sit below it. Every band is those two plus a little, rather than a round
 * number — the schematic is embedded in a tab beside other cards, so empty
 * canvas is not restful, it is the rest of the page being pushed down.
 */
const CHIP_CLEARANCE = 46;
const LABEL_CLEARANCE = 30;
const BREATHING = 12;

const MARGIN_TOP = CHIP_CLEARANCE + BREATHING;
const MARGIN_BOTTOM = LABEL_CLEARANCE;
/** Between rows: the row above's text, the row below's chips, and the
 *  row-change conveyor running down through both. */
const ROW_GAP = LABEL_CLEARANCE + CHIP_CLEARANCE + BREATHING;

const ROW_PITCH = STATION_H + ROW_GAP;

/** Rows of `MACHINE_LAYOUT`, top of the line first. It numbers from the bottom. */
const ROW_YS: number[] = Array.from(new Set(MACHINE_LAYOUT.map((m) => m.y))).sort((a, b) => b - a);

const ROW_OF_MACHINE: Record<string, number> = Object.fromEntries(
  MACHINE_LAYOUT.map((m) => [m.machineId, ROW_YS.indexOf(m.y)])
);

export const isConveyor = (machineId: string): boolean => MACHINE_META[machineId]?.type === 'conveyor';

export interface FlowNode {
  machineId: string;
  /** 0 = top row. */
  row: number;
  /** Position among the drawn stations of its row, 0 = first travelled. */
  col: number;
  /** Top-left corner of the station cell. */
  x: number;
  y: number;
  /** Centre of the station cell. */
  cx: number;
  cy: number;
  /** Position on the line, 1 = first machine. Counts conveyors, so it matches
   *  `MachineMeta.linePosition` rather than renumbering around them. */
  position: number;
}

export type FlowLinkKind = 'along-row' | 'row-change';

export interface FlowLink {
  /** The stations this link joins. Never a conveyor — those are the link. */
  from: string;
  to: string;
  /**
   * The conveyor doing the carrying, when the line has one here. Null means the
   * two machines hand over directly, which on this line is most of them.
   */
  conveyorId: string | null;
  kind: FlowLinkKind;
  /**
   * Always written upstream → downstream, so a single dash animation running
   * one direction reads as "forward" on every link, including the rows that
   * are drawn right-to-left.
   */
  d: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  /** Midpoint, for hanging a rate label or a belt off the link. */
  mx: number;
  my: number;
  /** Length, so a belt knows how many rollers it has room for. */
  length: number;
  /**
   * Direction of travel in degrees, 0 pointing right.
   *
   * Motion carries direction while the line is live, but a paused animation —
   * a screenshot, or a reader who has asked for reduced motion — leaves a row
   * that runs right to left indistinguishable from one that runs left to
   * right. A chevron rotated by this reads the same either way.
   */
  dirDeg: number;
}

export interface ProcessFlow {
  /** The fifteen machines drawn as stations, in process order. */
  nodes: FlowNode[];
  /** The fourteen sections between them, four of which are conveyors. */
  links: FlowLink[];
  byId: Record<string, FlowNode>;
  /** Which link a conveyor is drawn as, for click and status lookups. */
  linkOfConveyor: Record<string, FlowLink>;
  width: number;
  height: number;
}

interface Planned {
  machineId: string;
  /** The conveyor immediately upstream of this station, if the line has one. */
  incomingConveyor: string | null;
  position: number;
  row: number;
}

/**
 * Walk the line and fold each conveyor into the station that follows it.
 *
 * A conveyor only becomes a link when there is a station on both sides of it to
 * be the link between. Three arrangements have no such pair, and in each the
 * conveyor keeps a station of its own rather than being folded into nothing:
 * one at the very start of the line, one at the very end, and the second of two
 * in a row. This line has none of them, but the failure if it grew one would be
 * a machine silently absent from a diagram whose whole claim is that it draws
 * the line — the worst kind, because nothing looks wrong.
 */
function planStations(): Planned[] {
  const planned: Planned[] = [];
  let pendingConveyor: string | null = null;

  MACHINE_FLOW_ORDER.forEach((machineId, index) => {
    const foldable =
      isConveyor(machineId) && planned.length > 0 && pendingConveyor === null && index < MACHINE_FLOW_ORDER.length - 1;

    if (foldable) {
      pendingConveyor = machineId;
      return;
    }
    planned.push({
      machineId,
      incomingConveyor: pendingConveyor,
      position: index + 1,
      row: ROW_OF_MACHINE[machineId] ?? 0,
    });
    pendingConveyor = null;
  });

  return planned;
}

/** Stations drawn in each row, top row first. */
export const ROW_SIZES: number[] = (() => {
  const counts: number[] = ROW_YS.map(() => 0);
  for (const station of planStations()) counts[station.row] += 1;
  return counts;
})();

/**
 * Lay the line out as a boustrophedon: the top row runs left to right, the next
 * runs back right to left, and so on.
 *
 * The alternative — every row starting at the left, as the existing Line Status
 * tab draws it — leaves the end of one row and the start of the next at
 * opposite sides of the diagram, so the eye has to jump and the connector
 * between them would have to cross the whole width. Turning at the end of each
 * row means consecutive stations are always adjacent.
 *
 * Rows are no longer a fixed grid, because the gaps are not all the same width
 * once conveyors live in them. Each row is instead anchored on the turn: it
 * starts directly beneath wherever the row above finished, so every row change
 * stays a short vertical drop no matter how the widths worked out.
 */
export function buildProcessFlow(): ProcessFlow {
  const planned = planStations();

  const placed: Array<Planned & { cx: number; cy: number; col: number }> = [];
  let cursor = 0;
  let previousRow = -1;
  let colInRow = 0;

  planned.forEach((station) => {
    if (station.row !== previousRow) {
      // Turn: the new row begins directly under where the last one ended, so
      // the drop between them is vertical whatever the row widths are.
      previousRow = station.row;
      colInRow = 0;
    } else {
      const step = station.row % 2 === 0 ? 1 : -1;
      const gap = station.incomingConveyor ? CONVEYOR_GAP : PLAIN_GAP;
      cursor += step * (STATION_W + gap);
      colInRow += 1;
    }
    placed.push({
      ...station,
      col: colInRow,
      cx: cursor,
      cy: MARGIN_TOP + station.row * ROW_PITCH + STATION_H / 2,
    });
  });

  // Normalise: the walk above starts at zero and rows running right-to-left can
  // take it negative, so shift everything into the margin.
  const minCx = placed.reduce((m, p) => Math.min(m, p.cx), Infinity);
  const shift = MARGIN_LEFT + STATION_W / 2 - minCx;

  const nodes: FlowNode[] = placed.map((p) => ({
    machineId: p.machineId,
    row: p.row,
    col: p.col,
    x: p.cx + shift - STATION_W / 2,
    y: p.cy - STATION_H / 2,
    cx: p.cx + shift,
    cy: p.cy,
    position: p.position,
  }));

  const byId: Record<string, FlowNode> = {};
  for (const node of nodes) byId[node.machineId] = node;

  const links: FlowLink[] = [];
  const linkOfConveyor: Record<string, FlowLink> = {};

  for (let i = 0; i < nodes.length - 1; i += 1) {
    const a = nodes[i];
    const b = nodes[i + 1];
    const conveyorId = planned[i + 1].incomingConveyor;

    let link: FlowLink;
    if (a.row === b.row) {
      // Leave A by the face pointing at B and arrive on B's near face, so the
      // section spans the gap and nothing is drawn underneath a station.
      const forward = b.cx > a.cx;
      const x1 = forward ? a.x + STATION_W : a.x;
      const x2 = forward ? b.x : b.x + STATION_W;
      const y = a.cy;
      link = {
        from: a.machineId,
        to: b.machineId,
        conveyorId,
        kind: 'along-row',
        d: `M ${x1} ${y} L ${x2} ${y}`,
        x1,
        y1: y,
        x2,
        y2: y,
        mx: (x1 + x2) / 2,
        my: y,
        length: Math.abs(x2 - x1),
        dirDeg: forward ? 0 : 180,
      };
    } else {
      // The turn: same column by construction, so it is a plain vertical drop.
      const x = a.cx;
      const y1 = a.y + STATION_H;
      const y2 = b.y;
      link = {
        from: a.machineId,
        to: b.machineId,
        conveyorId,
        kind: 'row-change',
        d: `M ${x} ${y1} L ${x} ${y2}`,
        x1: x,
        y1,
        x2: x,
        y2,
        mx: x,
        my: (y1 + y2) / 2,
        length: Math.abs(y2 - y1),
        dirDeg: 90,
      };
    }

    links.push(link);
    if (conveyorId) linkOfConveyor[conveyorId] = link;
  }

  const maxX = nodes.reduce((m, n) => Math.max(m, n.x + STATION_W), 0);
  const width = maxX + MARGIN_RIGHT;
  const height = MARGIN_TOP + ROW_SIZES.length * STATION_H + (ROW_SIZES.length - 1) * ROW_GAP + MARGIN_BOTTOM;

  return { nodes, links, byId, linkOfConveyor, width, height };
}

/** Where a supply chip sits for a station: floating above it, joined by a stub. */
export function supplyAnchor(node: FlowNode): { x: number; y: number; lineY1: number; lineY2: number } {
  const y = node.y - 46;
  return { x: node.cx, y, lineY1: y + 20, lineY2: node.y };
}

/**
 * Where a supply chip sits for a conveyor, which has no station cell to hang
 * over. Belt lubricant feeds `CONVEYOR_1`, so this is a real case rather than a
 * defensive one — without it a material the line actually consumes would be
 * missing from a diagram that claims to draw them all.
 */
export function conveyorSupplyAnchor(link: FlowLink): { x: number; y: number; lineY1: number; lineY2: number } {
  const y = link.my - 56;
  return { x: link.mx, y, lineY1: y + 20, lineY2: link.my - BELT_HALF_HEIGHT };
}

/** Where finished product leaves the line. */
export function outfeedAnchor(flow: ProcessFlow): { x: number; y: number } | null {
  const last = flow.nodes[flow.nodes.length - 1];
  if (!last) return null;
  return { x: last.x + STATION_W, y: last.cy };
}
