import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { TAB_IDS } from '../lib/tabRouting';
import { MACHINE_FLOW_ORDER, MACHINE_META } from '../lib/machineMeta';
import { buildProcessFlow } from '../lib/processFlowLayout';
import { hasSymbol } from './processFlowSymbols';

const here = path.dirname(new URL(import.meta.url).pathname);
const read = (rel: string) => readFileSync(path.join(here, rel), 'utf8');

const schematic = read('ProcessFlowSchematic.tsx');
const app = read('../App.tsx');
const css = read('../index.css');

describe('it is the line schematic, not a view beside one', () => {
  it('is what Line Status draws', () => {
    expect(app).toContain("import ProcessFlowSchematic from './components/ProcessFlowSchematic'");
    const tower = app.slice(app.indexOf("activeTab === 'tower' && ("));
    expect(tower.slice(0, 3000)).toContain('<ProcessFlowSchematic');
  });

  it('left no second copy of the line behind it', () => {
    // The tile grid it replaced drew the same 19 machines from the same map. Two
    // pictures of one line is two things to keep true, and the stale one is the
    // one someone screenshots.
    expect(existsSync(path.join(here, 'LineSchematic.tsx'))).toBe(false);
    expect(app).not.toContain('LineSchematic');
    expect(TAB_IDS).not.toContain('flow');
  });

  it('is boundaried, so a throw here does not take the app with it', () => {
    const mount = app.slice(0, app.indexOf('<ProcessFlowSchematic'));
    expect(mount.slice(-200)).toContain('<ErrorBoundary>');
  });

  it('opens the machine detail card from the same handler Line Status already used', () => {
    expect(schematic).toContain('onMachineClick');
    expect(app).toMatch(/<ProcessFlowSchematic[\s\S]*?onMachineClick=\{handleMachineClick\}/);
    expect(app.match(/<MachineDetailCard/g) ?? []).toHaveLength(1);
  });

  it('feeds on the same live stream, not a second fetch of its own', () => {
    expect(schematic).not.toContain('EventSource');
    expect(schematic).not.toContain('fetch(');
    expect(app).toMatch(/<ProcessFlowSchematic[\s\S]*?inventory=\{sseLive\.inventory\}/);
  });

  it('says where its colours come from, as the grid it replaced did', () => {
    expect(schematic).toContain('<DataSourceBadge');
    expect(schematic).toMatch(/sources=\{\['SCADA', 'PLC', 'Historian'\]\}/);
  });

  it('draws a skeleton before the first frame rather than a line of idle machines', () => {
    // "Idle" is a status this line can genuinely be in, so a grey line before
    // the stream connects would be a claim rather than an absence.
    expect(schematic).toContain('<Skeleton');
    expect(app).toMatch(/<ProcessFlowSchematic[\s\S]*?loading=\{!sseLive\.hasConnected\}/);
  });
});

describe('every machine is on it', () => {
  const flow = buildProcessFlow();

  it('draws the whole line, so nothing is quietly missing', () => {
    // Fifteen machines are stations and four are the belts between them, but
    // all nineteen have to be somewhere. A conveyor that stopped being a box
    // and did not become a belt would just be gone.
    const drawn = new Set([...flow.nodes.map((n) => n.machineId), ...Object.keys(flow.linkOfConveyor)]);
    expect(drawn).toEqual(new Set(MACHINE_FLOW_ORDER));
  });

  it('lets a conveyor be clicked and opened like the machine it is', () => {
    // The belt carries the same handler as a station, so the detail card works
    // from either. Losing this would make four machines unreachable here.
    expect(schematic).toContain('handleActivate(conveyorId)');
    expect(schematic).toContain('handleKey(conveyorId)');
    expect(schematic).toMatch(/aria-label=\{`\$\{meta\?\.displayName \?\? conveyorId\}, conveyor between/);
  });

  it('counts conveyors in the running tally rather than quietly dropping them', () => {
    const tally = schematic.slice(schematic.indexOf('const counts = useMemo'));
    expect(tally.slice(0, 400)).toContain('MACHINE_FLOW_ORDER');
    expect(schematic).toContain('/{MACHINE_FLOW_ORDER.length}');
  });

  it('makes each machine operable by keyboard, not just by mouse', () => {
    expect(schematic).toContain('tabIndex={0}');
    expect(schematic).toContain('onKeyDown');
    expect(schematic).toMatch(/role="button"/);
    expect(schematic).toContain('aria-label');
  });

  it('walks them in the order product does, not belts first and machines after', () => {
    // Tab order is DOM order in SVG. Drawing all the sections and then all the
    // stations would have someone tabbing through four conveyors scattered down
    // the line before reaching the depalletiser, which is not a line anyone
    // could follow. Emitting each section just before the station it arrives at
    // gives the reading order and the paint order at once.
    expect(schematic).not.toMatch(/flow\.links\.map\(renderLink\)/);
    const stations = schematic.slice(schematic.indexOf('flow.nodes.map((node, index)'));
    expect(stations).toContain('renderLink(flow.links[index - 1])');
  });

  it('boxes a conveyor from its own direction, so a vertical one stays clickable', () => {
    // The four conveyors on this line all run along a row, and a belt taken as
    // horizontal when it runs down the turn collapses to a zero-width hit area:
    // one machine on the diagram nobody can click, and nothing to see.
    const belt = schematic.slice(schematic.indexOf('const padX ='));
    expect(belt.slice(0, 400)).toMatch(/Math\.abs\(px\)/);
    expect(belt.slice(0, 400)).toMatch(/Math\.abs\(py\)/);
    expect(schematic).toMatch(/<rect x=\{boxX\} y=\{boxY\} width=\{boxW\} height=\{boxH\} fill="transparent" \/>/);
  });
});

describe('a machine is drawn as the machine it is', () => {
  it('has a symbol for every type in the registry, so none falls back to a box', () => {
    const types = new Set(Object.values(MACHINE_META).map((m) => m.type));
    expect(types.size).toBeGreaterThan(10);
    for (const type of types) {
      expect(hasSymbol(type), `${type} has no schematic symbol`).toBe(true);
    }
  });

  it('draws the symbol rather than the rectangle it replaced', () => {
    expect(schematic).toContain('<StationSymbol');
    // The old station box was an opaque fill with a status stroke. What is left
    // is a transparent hit area, which must not become a visible box again.
    expect(schematic).not.toMatch(/fill=\{CANVAS\.stationFill\}\s+stroke=\{status/);
  });

  it('keeps a hit area, because strokes are almost nothing to click', () => {
    expect(schematic).toContain('fill="transparent"');
  });
});

describe('the animation stays off the main thread', () => {
  it('drives motion from CSS rather than a per-frame render', () => {
    // ~18 connectors re-rendering every frame would put a repaint on the main
    // thread for each one; the compositor can run a dashoffset on its own.
    expect(schematic).not.toMatch(/requestAnimationFrame|setInterval/);
    expect(css).toContain('@keyframes pfFlowDash');
    expect(schematic).toContain('--pf-duration');
  });

  it('honours a request for reduced motion', () => {
    const query = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(query).toContain('.pf-flow');
    expect(query).toContain('.pf-feed');
    expect(query).toContain('animation: none');
  });

  it('still says the rate in words when nothing is moving', () => {
    // Motion encodes rate here, so removing it would remove information unless
    // the number is on screen too.
    expect(schematic).toContain('state.ratePerMin');
    expect(schematic).toContain('dirDeg');
  });
});

describe('it does not claim to know what it cannot', () => {
  it('shows no buffer, WIP, or queue level, because none is published', () => {
    // The cascade simulator computes buffer levels and drops them at emit, so
    // nothing downstream has them. A gauge here would be invented.
    for (const invented of ['buffer_level', 'bufferLevel', 'wip', 'queue_depth', 'fill_ratio']) {
      expect(schematic.toLowerCase()).not.toContain(invented.toLowerCase());
    }
  });

  it('says out loud that the between-station quantity is not shown', () => {
    expect(schematic).toMatch(/not published/i);
  });

  it('counts only the material feeds it actually drew', () => {
    // A material naming a machine this line does not have is skipped at draw
    // time. Counting the raw map would promise "6 feeding" and put five chips
    // on screen, and the reader would go looking for the sixth.
    expect(schematic).toContain('const feedingCount = drawnFeeds.length');
    expect(schematic).toContain('drawnFeeds.map(');
  });

  it('draws a material feed only where the data says which station it feeds', () => {
    // `feeds_machine_id` is what makes these lines real rather than decorative.
    const state = read('../lib/processFlowState.ts');
    expect(state).toContain('feeds_machine_id');
    expect(state).toMatch(/if \(!machineId\) continue/);
  });
});
