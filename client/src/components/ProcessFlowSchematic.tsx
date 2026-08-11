/**
 * The live line schematic: the whole line as one continuous path, with product
 * moving along it and material being drawn into it.
 * Used by `App` on the Line Status tab; clicking a machine opens the detail card.
 *
 * This replaced a tile grid that drew the line as three disconnected rows of
 * boxes reading left to right, so the end of one row and the start of the next
 * sat at opposite sides of the screen and the belts between machines were not
 * drawn at all. It also inferred "cascade risk" client-side by colouring the
 * three machines downstream of anything stopped, which was a guess sitting next
 * to measurements. The stream already carries starved and blocked, which is the
 * same question answered by the line itself, so the guess is gone.
 *
 * Drawn on a light canvas, a shade off the card it sits in. It was near-black
 * at first, on the argument that a control room is dark and emissive colour on
 * black is how a status reads from across a room. That is true of a control
 * room and false of this app: the schematic is embedded in a white page, so the
 * effect was not a plant view but a hole in the middle of the Line Status tab,
 * with its own contrast rules and its own idea of what grey means. Matching the
 * page costs the emissive look and buys a diagram that reads as part of the
 * thing it belongs to.
 *
 * The palette is the slate ramp the rest of the app uses, and machine colours
 * are the same `STATUS_COLORS` every other surface uses, so a stopped machine
 * is the same red here as in the alert beside it.
 *
 * Two things follow from drawing the line the way the line is built. Machines
 * are drawn as the equipment they are — a filler as a bowl and valves, a
 * palletiser as an arm over a pallet — because on a real schematic the shape is
 * how you find the machine you are looking for without reading every label. And
 * the four conveyors are drawn as the belts between the stations rather than as
 * stations of their own, because that is what they are. Both stay clickable and
 * carry their own status; only the shape changed.
 */
import { Fragment, memo, useCallback, useMemo } from 'react';
import { Skeleton } from '@databricks/appkit-ui/react';
import DataSourceBadge from './DataSourceBadge';
import { MACHINE_LAYOUT } from '../lib/constants';
import { MACHINE_META, MACHINE_FLOW_ORDER } from '../lib/machineMeta';
import {
  buildProcessFlow,
  supplyAnchor,
  conveyorSupplyAnchor,
  outfeedAnchor,
  STATION_W,
  STATION_H,
  BELT_HALF_HEIGHT,
  type FlowLink,
} from '../lib/processFlowLayout';
import { StationSymbol, SYMBOL_W, SYMBOL_H } from './processFlowSymbols';
import {
  asFlowStatus,
  dashDurationSeconds,
  flowThrough,
  supplyFeedsByMachine,
  FLOW_STATE_COLORS,
  FLOW_STATE_HINTS,
  FLOW_STATE_LABELS,
  FLOW_TONE_COLORS,
  SUPPLY_COLORS,
  type LiveStation,
  type FlowMachineStatus,
} from '../lib/processFlowState';
import type { InventoryItem } from '../lib/useSSELiveData';

export interface ProcessFlowSchematicProps {
  machineStates: Map<string, LiveStation>;
  inventory: InventoryItem[];
  kpi: Record<string, unknown> | null;
  selectedMachine: string | null;
  onMachineClick: (machineId: string) => void;
  /** No stream yet. Draws a skeleton rather than a line of grey machines. */
  loading?: boolean;
  /** Connected, but between values. Draws the line with the pulse held. */
  waiting?: boolean;
}

/** Carried over from the tile grid: the schematic's colours come from these. */
const SCHEMATIC_SOURCE_DETAILS: Record<string, string> = {
  SCADA: 'Primary machine state, speed, and alarm values used for node colors.',
  PLC: 'Discrete equipment status/commands reflected in live machine tiles.',
  Historian: 'Short-window time-series used for trend/risk propagation hints.',
};

const flow = buildProcessFlow();

const SHORT_LABEL: Record<string, string> = Object.fromEntries(MACHINE_LAYOUT.map((m) => [m.machineId, m.label]));

/** The dash period, matched to the dasharray below so one cycle moves exactly one dash. */
const DASH_PERIOD = 24;
const DASH_ARRAY = '7 17';

/**
 * The slate ramp, so the diagram is built from the same greys as the page. The
 * canvas is a step off white and the stations are white, which puts the
 * equipment in front of the surface it is drawn on rather than behind it.
 */
const CANVAS = {
  bg: '#f8fafc',
  grid: '#e2e8f0',
  pipe: '#94a3b8',
  stationFill: '#ffffff',
  stationEdge: '#cbd5e1',
  text: '#0f172a',
  dim: '#64748b',
  /** A step below `dim`, for asides that should be readable but not read first. */
  faint: '#94a3b8',
  /** Selection. Sky reads as "chosen" without colliding with any machine state. */
  selected: '#0284c7',
  /** The streaming pulse. Cyan-600 rather than the cyan-400 the dark canvas
   *  used, which on a near-white background was barely a dot. */
  live: '#0891b2',
};

const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace';

function num(source: Record<string, unknown> | null, key: string): number | null {
  const value = source?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function compact(value: number): string {
  if (Math.abs(value) >= 10000) return `${Math.round(value / 1000)}k`;
  if (Math.abs(value) >= 100) return String(Math.round(value));
  return String(Math.round(value * 10) / 10);
}

/** Material ids are warehouse codes and can be long; the chip has ~11 characters. */
function shortMaterial(id: string): string {
  const pretty = id.replace(/_/g, ' ');
  return pretty.length <= 11 ? pretty : `${pretty.slice(0, 10)}…`;
}

function ProcessFlowSchematic({
  machineStates,
  inventory,
  kpi,
  selectedMachine,
  onMachineClick,
  loading = false,
  waiting = false,
}: ProcessFlowSchematicProps) {
  const feeds = useMemo(() => supplyFeedsByMachine(inventory, machineStates), [inventory, machineStates]);

  const handleActivate = useCallback((machineId: string) => () => onMachineClick(machineId), [onMachineClick]);

  const handleKey = useCallback(
    (machineId: string) => (event: React.KeyboardEvent) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onMachineClick(machineId);
      }
    },
    [onMachineClick]
  );

  // Counted over every machine on the line, conveyors included. They are drawn
  // as belts rather than boxes but they still run, stop and raise alarms, and a
  // "running 15 of 15" that quietly excluded them would be wrong.
  const counts = useMemo(() => {
    const tally: Record<string, number> = {};
    for (const machineId of MACHINE_FLOW_ORDER) {
      const status = asFlowStatus(machineStates.get(machineId)?.status);
      tally[status] = (tally[status] ?? 0) + 1;
    }
    return tally;
  }, [machineStates]);

  // Only the feeds that end up on the diagram. A material naming a machine this
  // line does not have is skipped when drawing, so counting the raw map would
  // promise a chip the reader then cannot find.
  const drawnFeeds = useMemo(
    () => [...feeds.entries()].filter(([machineId]) => flow.byId[machineId] || flow.linkOfConveyor[machineId]),
    [feeds]
  );

  const cumulativeCases = num(kpi, 'cumulative_cases');
  const feedingCount = drawnFeeds.length;
  const outfeed = outfeedAnchor(flow);
  const running = counts.running ?? 0;

  // Before the first frame arrives every machine would read "idle", which is a
  // status this line can genuinely be in. A skeleton cannot be mistaken for one.
  if (loading) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border bg-muted/30 p-4"
        style={{ minHeight: '320px' }}
        aria-label="Production line schematic loading"
      >
        <Skeleton className="h-[300px] w-full rounded" />
      </div>
    );
  }

  /**
   * A section of line, drawn under the stations it joins.
   *
   * Every section is a belt, because on a line this is a belt: product does not
   * teleport between a filler and a capper. Four of them are conveyors the
   * plant maintains as assets in their own right, and those are the ones that
   * carry a status colour, a name and a click. The rest are drawn dim and
   * unlabelled — transport that exists but that nobody is metering.
   */
  const renderLink = (link: FlowLink) => {
    const conveyorId = link.conveyorId;
    const conveyor = conveyorId ? machineStates.get(conveyorId) : undefined;
    const state = flowThrough(machineStates.get(link.from), conveyor, machineStates.get(link.to));
    const toneColor = FLOW_TONE_COLORS[state.tone];
    const duration = dashDurationSeconds(state.speedPct);
    const status = asFlowStatus(conveyor?.status);
    const key = `${link.from}->${link.to}`;

    // Rails run either side of the centre line, so the same code draws a belt
    // along a row and the one that carries the turn down to the next.
    const length = Math.max(link.length, 1);
    const ux = (link.x2 - link.x1) / length;
    const uy = (link.y2 - link.y1) / length;
    const px = -uy;
    const py = ux;
    const rail = (sign: number) =>
      `M ${link.x1 + px * BELT_HALF_HEIGHT * sign} ${link.y1 + py * BELT_HALF_HEIGHT * sign}` +
      ` L ${link.x2 + px * BELT_HALF_HEIGHT * sign} ${link.y2 + py * BELT_HALF_HEIGHT * sign}`;

    const rollerCount = Math.max(2, Math.floor(length / 16));
    const rollerStep = length / (rollerCount + 1);

    const railColor = conveyorId ? (status === 'idle' ? CANVAS.stationEdge : FLOW_STATE_COLORS[status]) : CANVAS.pipe;

    const belt = (
      <>
        <path
          d={`${rail(1)} ${rail(-1)}`}
          stroke={railColor}
          strokeWidth={conveyorId ? 1.6 : 1.2}
          strokeLinecap="round"
          fill="none"
          opacity={conveyorId ? 1 : 0.9}
        />
        <g stroke={CANVAS.pipe} strokeWidth="1" fill="none" opacity={conveyorId ? 1 : 0.7}>
          {Array.from({ length: rollerCount }, (_, i) => (
            <circle
              key={i}
              cx={link.x1 + ux * rollerStep * (i + 1)}
              cy={link.y1 + uy * rollerStep * (i + 1)}
              r={BELT_HALF_HEIGHT - 2.5}
            />
          ))}
        </g>
        <path
          d={link.d}
          fill="none"
          stroke={toneColor}
          strokeWidth={state.moving ? 3 : 2}
          strokeLinecap="round"
          strokeDasharray={DASH_ARRAY}
          className={state.moving ? 'pf-flow' : undefined}
          style={
            state.moving
              ? ({ '--pf-duration': `${duration}s`, '--pf-period': DASH_PERIOD } as React.CSSProperties)
              : { opacity: 0.55 }
          }
        />
        <polygon
          points="-3.5,-4 4,0 -3.5,4"
          fill={toneColor}
          opacity={state.moving ? 0.95 : 0.4}
          transform={`translate(${link.mx} ${link.my}) rotate(${link.dirDeg})`}
        />
      </>
    );

    if (!conveyorId) {
      return (
        <g key={key}>
          <title>{`${SHORT_LABEL[link.from] ?? link.from} → ${SHORT_LABEL[link.to] ?? link.to}: ${state.reason}`}</title>
          {belt}
        </g>
      );
    }

    const meta = MACHINE_META[conveyorId];
    const isSelected = selectedMachine === conveyorId;
    const alarms = conveyor?.alarm_count ?? 0;

    // Box the belt from its own direction rather than assuming a horizontal
    // one. Every conveyor on this line happens to sit within a row, but a
    // vertical one taken as horizontal would collapse to a zero-width hit area
    // and become the one machine on the diagram nobody could click.
    const padX = Math.abs(px) * (BELT_HALF_HEIGHT + 6);
    const padY = Math.abs(py) * (BELT_HALF_HEIGHT + 6);
    const boxX = Math.min(link.x1, link.x2) - padX;
    const boxY = Math.min(link.y1, link.y2) - padY;
    const boxW = Math.abs(link.x2 - link.x1) + padX * 2;
    const boxH = Math.abs(link.y2 - link.y1) + padY * 2;
    const vertical = Math.abs(uy) > Math.abs(ux);

    return (
      <g
        key={key}
        role="button"
        tabIndex={0}
        aria-label={`${meta?.displayName ?? conveyorId}, conveyor between ${SHORT_LABEL[link.from] ?? link.from} and ${SHORT_LABEL[link.to] ?? link.to}. ${FLOW_STATE_LABELS[status]}.${alarms > 0 ? ` ${alarms} alarm${alarms > 1 ? 's' : ''}.` : ''}`}
        aria-pressed={isSelected}
        className="cursor-pointer focus:outline-none"
        onClick={handleActivate(conveyorId)}
        onKeyDown={handleKey(conveyorId)}
      >
        <title>
          {`${meta?.displayName ?? conveyorId} — ${FLOW_STATE_LABELS[status]}. ${state.reason}` +
            (meta?.nominalRate != null ? ` Design ${meta.nominalRate} ${meta.rateUnit}.` : '')}
        </title>

        {isSelected && (
          <rect
            x={boxX - 2}
            y={boxY - 2}
            width={boxW + 4}
            height={boxH + 4}
            rx="5"
            fill="none"
            stroke={CANVAS.selected}
            strokeWidth="2"
          />
        )}

        {/* Hit area: strokes alone are a poor click target. */}
        <rect x={boxX} y={boxY} width={boxW} height={boxH} fill="transparent" />

        {belt}

        <text
          x={vertical ? link.mx + BELT_HALF_HEIGHT + 6 : link.mx}
          y={vertical ? link.my + 3 : link.my + BELT_HALF_HEIGHT + 13}
          textAnchor={vertical ? 'start' : 'middle'}
          fontSize="8.5"
          fill={status === 'running' || status === 'idle' ? CANVAS.dim : railColor}
          fontFamily={MONO}
        >
          {SHORT_LABEL[conveyorId] ?? conveyorId}
          {state.ratePerMin != null && state.ratePerMin > 0 ? ` · ${compact(state.ratePerMin)}/min` : ''}
        </text>

        {alarms > 0 && (
          <>
            <circle cx={boxX + 6} cy={boxY + 6} r="6.5" fill="#ef4444" />
            <text
              x={boxX + 6}
              y={boxY + 9}
              textAnchor="middle"
              fontSize="8"
              fontWeight="700"
              fill="#fff"
              fontFamily={MONO}
            >
              {alarms}
            </text>
          </>
        )}
      </g>
    );
  };

  // One card, one header. It used to be a titled card wrapping a bordered
  // canvas, which stacked two headers and two borders down the top of the tab
  // to say "Production Line" above "Live line schematic" — the same sentence
  // twice, for about fifty pixels.
  return (
    <div className="relative overflow-x-auto rounded-lg border border-border" style={{ background: CANVAS.bg }}>
      <div
        className="flex items-center justify-between px-3 py-2 text-[11px] uppercase tracking-widest"
        style={{ color: CANVAS.dim, borderBottom: `1px solid ${CANVAS.grid}` }}
      >
        <span>Live line schematic — click any machine</span>
        <span className="flex items-center gap-3 normal-case tracking-normal">
          <span>
            {running}/{MACHINE_FLOW_ORDER.length} running
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className={waiting ? 'h-1.5 w-1.5 rounded-full' : 'h-1.5 w-1.5 rounded-full pf-pulse'}
              style={{ background: waiting ? CANVAS.dim : CANVAS.live }}
              aria-hidden
            />
            {waiting ? 'Waiting for the next value' : 'Streaming'}
          </span>
          <DataSourceBadge sources={['SCADA', 'PLC', 'Historian']} details={SCHEMATIC_SOURCE_DETAILS} />
        </span>
      </div>

      <svg
        viewBox={`0 0 ${flow.width} ${flow.height}`}
        className="block h-auto w-full min-w-[900px]"
        role="group"
        aria-label="Live process flow schematic of the production line"
      >
        <defs>
          <pattern id="pfGrid" width="26" height="26" patternUnits="userSpaceOnUse">
            <path d="M 26 0 L 0 0 0 26" fill="none" stroke={CANVAS.grid} strokeWidth="0.5" opacity="0.55" />
          </pattern>
          <marker id="pfArrowHead" markerWidth="7" markerHeight="7" refX="5.5" refY="3" orient="auto">
            <path d="M 0 0 L 6 3 L 0 6 z" fill={CANVAS.dim} />
          </marker>
        </defs>

        <rect width={flow.width} height={flow.height} fill="url(#pfGrid)" />

        {/* Material drawn into the machines that consume it. Conveyors take
              belt lubricant, so a feed can land on a belt as well as a station. */}
        <g>
          {drawnFeeds.map(([machineId, stationFeeds]) => {
            const node = flow.byId[machineId];
            const link = flow.linkOfConveyor[machineId];
            const anchor = node ? supplyAnchor(node) : conveyorSupplyAnchor(link);
            const lead = stationFeeds[0];
            const color = SUPPLY_COLORS[lead.urgency];
            const chipW = 92;
            const chipH = 20;

            return (
              <g key={`feed-${machineId}`}>
                <title>
                  {`${lead.materialId} feeds ${SHORT_LABEL[machineId] ?? machineId}. ` +
                    `${compact(lead.onHand)} on hand, drawn at ${compact(lead.consumptionPerHour)}/h` +
                    (lead.hoursLeft != null ? `, about ${lead.hoursLeft}h left.` : '.') +
                    (stationFeeds.length > 1 ? ` +${stationFeeds.length - 1} more material here.` : '')}
                </title>
                <path
                  d={`M ${anchor.x} ${anchor.lineY1} L ${anchor.x} ${anchor.lineY2}`}
                  fill="none"
                  stroke={color}
                  strokeWidth="1.5"
                  strokeDasharray="3 6"
                  opacity={lead.moving ? 0.95 : 0.3}
                  className={lead.moving ? 'pf-feed' : undefined}
                  style={
                    lead.moving ? ({ '--pf-duration': '2.6s', '--pf-period': 9 } as React.CSSProperties) : undefined
                  }
                />
                <rect
                  x={anchor.x - chipW / 2}
                  y={anchor.y}
                  width={chipW}
                  height={chipH}
                  rx="4"
                  fill={CANVAS.stationFill}
                  stroke={color}
                  strokeWidth="1"
                  opacity={lead.urgency === 'ok' ? 0.75 : 1}
                />
                <text
                  x={anchor.x}
                  y={anchor.y + 13.5}
                  textAnchor="middle"
                  fontSize="8.5"
                  fill={color}
                  fontFamily={MONO}
                >
                  {shortMaterial(lead.materialId)}
                  {stationFeeds.length > 1 ? ` +${stationFeeds.length - 1}` : ''}
                </text>
              </g>
            );
          })}
        </g>

        {/* Where empty bottles come in and finished pallets leave. */}
        <g fontFamily={MONO}>
          {flow.nodes[0] && (
            <>
              <path
                d={`M ${flow.nodes[0].x - 46} ${flow.nodes[0].cy} L ${flow.nodes[0].x - 5} ${flow.nodes[0].cy}`}
                stroke={CANVAS.dim}
                strokeWidth="1.5"
                markerEnd="url(#pfArrowHead)"
                fill="none"
              />
              <text x={flow.nodes[0].x - 46} y={flow.nodes[0].cy - 9} fontSize="8.5" fill={CANVAS.dim}>
                PALLETS IN
              </text>
            </>
          )}
          {outfeed && (
            <>
              <path
                d={`M ${outfeed.x + 5} ${outfeed.y} L ${outfeed.x + 46} ${outfeed.y}`}
                stroke={CANVAS.dim}
                strokeWidth="1.5"
                markerEnd="url(#pfArrowHead)"
                fill="none"
              />
              <text x={outfeed.x + 5} y={outfeed.y - 9} fontSize="8.5" fill={CANVAS.dim}>
                {cumulativeCases != null ? `${compact(cumulativeCases)} CASES` : 'CASES OUT'}
              </text>
            </>
          )}
        </g>

        {/* Machines and the sections between them, emitted in the order
              product travels. That order is also the order a keyboard walks
              them, so tabbing follows the line instead of jumping between
              every belt and then every machine. Stations still paint over
              their sections, because each station comes after the section
              that arrives at it. */}
        <g>
          {flow.nodes.map((node, index) => {
            const live = machineStates.get(node.machineId);
            const status: FlowMachineStatus = asFlowStatus(live?.status);
            const color = FLOW_STATE_COLORS[status];
            const meta = MACHINE_META[node.machineId];
            const label = SHORT_LABEL[node.machineId] ?? node.machineId;
            const isSelected = selectedMachine === node.machineId;
            const alarms = live?.alarm_count ?? 0;
            const throughput = live?.throughput_per_min ?? 0;
            const speed = live?.speed_pct;
            const symbolX = node.x + (STATION_W - SYMBOL_W) / 2;
            const healthy = status === 'running' || status === 'idle';

            return (
              <Fragment key={node.machineId}>
                {index > 0 && renderLink(flow.links[index - 1])}
                <g
                  role="button"
                  tabIndex={0}
                  aria-label={`${meta?.displayName ?? label}, station ${node.position} of ${MACHINE_FLOW_ORDER.length}. ${FLOW_STATE_LABELS[status]}.${alarms > 0 ? ` ${alarms} alarm${alarms > 1 ? 's' : ''}.` : ''}`}
                  aria-pressed={isSelected}
                  className="cursor-pointer focus:outline-none"
                  onClick={handleActivate(node.machineId)}
                  onKeyDown={handleKey(node.machineId)}
                >
                  <title>
                    {`${meta?.displayName ?? label} — ${FLOW_STATE_LABELS[status]}. ${FLOW_STATE_HINTS[status]}` +
                      (meta ? ` ${meta.role}` : '') +
                      (meta?.nominalRate != null ? ` Design rate ${meta.nominalRate} ${meta.rateUnit}.` : '')}
                  </title>

                  {isSelected && (
                    <rect
                      x={node.x - 3}
                      y={node.y - 3}
                      width={STATION_W + 6}
                      height={STATION_H + 6}
                      rx="9"
                      fill="none"
                      stroke={CANVAS.selected}
                      strokeWidth="2"
                    />
                  )}

                  {/* Hit area. The symbol is strokes, which is almost nothing to
                      aim at, and the label sits below it. */}
                  <rect x={node.x} y={node.y} width={STATION_W} height={STATION_H} rx="8" fill="transparent" />

                  {/* A machine in trouble gets a lit backing, so the eye finds
                      the fault before it reads a single label. A healthy line
                      stays clean line-art. */}
                  {!healthy && (
                    <rect
                      x={symbolX - 3}
                      y={node.y - 1}
                      width={SYMBOL_W + 6}
                      height={SYMBOL_H + 6}
                      rx="6"
                      fill={color}
                      opacity={status === 'stopped' ? 0.18 : 0.11}
                    />
                  )}

                  <StationSymbol
                    type={meta?.type ?? ''}
                    x={symbolX}
                    y={node.y + 2}
                    color={status === 'idle' ? CANVAS.stationEdge : color}
                    dim={CANVAS.dim}
                  />

                  <text
                    x={node.cx}
                    y={node.y + SYMBOL_H + 15}
                    textAnchor="middle"
                    fontSize="10.5"
                    fontWeight="600"
                    fill={CANVAS.text}
                    fontFamily={MONO}
                  >
                    {label}
                  </text>
                  <text
                    x={node.cx}
                    y={node.y + SYMBOL_H + 27}
                    textAnchor="middle"
                    fontSize="9.5"
                    fill={color}
                    fontFamily={MONO}
                  >
                    {/* Plainly "/min". The stream carries items per minute,
                        which is not the same as each machine's nominal unit —
                        printing that unit here would label the number as
                        something it is not. The station's own rate unit is in
                        the tooltip and the detail card. */}
                    {throughput > 0 ? `${compact(throughput)} /min` : FLOW_STATE_LABELS[status].split(' —')[0]}
                  </text>
                  <text
                    x={node.cx}
                    y={node.y + SYMBOL_H + 38}
                    textAnchor="middle"
                    fontSize="8"
                    fill={CANVAS.dim}
                    fontFamily={MONO}
                  >
                    {speed != null ? `${Math.round(speed)}% of design` : '—'}
                  </text>

                  <text
                    x={node.x + STATION_W - 2}
                    y={node.y + 9}
                    textAnchor="end"
                    fontSize="8"
                    fill={CANVAS.dim}
                    fontFamily={MONO}
                  >
                    {node.position}
                  </text>

                  {alarms > 0 && (
                    <>
                      <circle cx={node.x + 5} cy={node.y + 3} r="6.5" fill="#ef4444" />
                      <text
                        x={node.x + 5}
                        y={node.y + 6}
                        textAnchor="middle"
                        fontSize="8"
                        fontWeight="700"
                        fill="#fff"
                        fontFamily={MONO}
                      >
                        {alarms}
                      </text>
                    </>
                  )}
                </g>
              </Fragment>
            );
          })}
        </g>
      </svg>

      <div
        className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-3 py-2 text-[11px]"
        style={{ color: CANVAS.dim, borderTop: `1px solid ${CANVAS.grid}` }}
      >
        {(['running', 'degraded', 'starved', 'blocked', 'stopped'] as FlowMachineStatus[]).map((status) => (
          <span key={status} className="flex items-center gap-1.5" title={FLOW_STATE_HINTS[status]}>
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: FLOW_STATE_COLORS[status] }}
              aria-hidden
            />
            {FLOW_STATE_LABELS[status]}
          </span>
        ))}
        <span
          className="flex items-center gap-1.5"
          title="Conveyors are drawn as the belts between machines, and are clickable like any other machine."
        >
          <svg width="24" height="10" aria-hidden>
            <path d="M 1 2 H 23 M 1 8 H 23" stroke={CANVAS.text} strokeWidth="1.2" strokeLinecap="round" fill="none" />
            <circle cx="8" cy="5" r="2" stroke={CANVAS.dim} strokeWidth="0.8" fill="none" />
            <circle cx="16" cy="5" r="2" stroke={CANVAS.dim} strokeWidth="0.8" fill="none" />
          </svg>
          Conveyor (4)
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="22" height="6" aria-hidden>
            <line x1="0" y1="3" x2="22" y2="3" stroke={SUPPLY_COLORS.ok} strokeWidth="1.5" strokeDasharray="3 4" />
          </svg>
          Material feed ({feedingCount} machine{feedingCount === 1 ? '' : 's'})
        </span>
        {/* The one thing worth saying in the margin. Dashes move at a measured
              speed, so it is fair to ask how much product is standing between
              two machines — and the honest answer is that the simulator tracks
              those buffers but does not publish the levels, so nothing here
              knows them. Said once, quietly, rather than in a panel below. */}
        <span className="ml-auto text-right leading-tight" style={{ color: CANVAS.faint }}>
          Motion = measured speed · buffer levels not published
        </span>
      </div>
    </div>
  );
}

export default memo(ProcessFlowSchematic);
