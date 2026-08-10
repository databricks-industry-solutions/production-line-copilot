/**
 * Schematic symbols for each kind of machine on the line.
 * Used by `ProcessFlowSchematic` to draw a station as the device it is rather than as
 * a labelled rectangle.
 *
 * Every symbol is drawn inside the same {@link SYMBOL_W} × {@link SYMBOL_H} box
 * with its origin at the top-left, so the caller can place one by translating
 * to a corner and never has to know what it contains. Strokes only, no fills
 * that would trap the background colour, and one stroke width throughout: these
 * are read at about a centimetre across on a dark canvas, and detail that does
 * not survive that size is just noise that slows the picture down.
 *
 * The convention, borrowed from the P&ID drawings these imitate: the machine is
 * drawn in the status colour, and whatever the machine is acting on — a bottle,
 * a case, a pallet — is drawn dim. So the eye picks up the equipment and its
 * state first, and the product only when it looks closer.
 */
import type { ReactElement } from 'react';
import type { MachineType } from '../lib/machineMeta';

export const SYMBOL_W = 44;
export const SYMBOL_H = 32;

interface SymbolProps {
  /** Status colour: the equipment itself is drawn in this. */
  color: string;
  /** Muted colour for product and secondary detail. */
  dim: string;
}

const STROKE = 1.4;
const THIN = 1;

/** A bottle, used by the stations that handle loose bottles. */
function Bottle({ x, y, dim, h = 14 }: { x: number; y: number; dim: string; h?: number }) {
  const neck = h * 0.28;
  return (
    <path
      d={`M ${x - 1.6} ${y} v ${neck} l -2.4 2 v ${h - neck - 2} h ${8} v ${-(h - neck - 2)} l -2.4 -2 v ${-neck} z`}
      fill="none"
      stroke={dim}
      strokeWidth={THIN}
      strokeLinejoin="round"
    />
  );
}

/** A case or carton, used by the stations at the packing end. */
function Case({ x, y, w, h, dim }: { x: number; y: number; w: number; h: number; dim: string }) {
  return (
    <g fill="none" stroke={dim} strokeWidth={THIN} strokeLinejoin="round">
      <rect x={x} y={y} width={w} height={h} rx="1" />
      <path d={`M ${x} ${y + h * 0.38} h ${w}`} />
    </g>
  );
}

const SYMBOLS: Record<MachineType, (props: SymbolProps) => ReactElement> = {
  /** Layers lifted off an incoming pallet: the stack goes down, product goes on. */
  depalletizer: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={dim} strokeWidth={THIN}>
        <rect x="5" y="13" width="19" height="6" rx="1" />
        <rect x="5" y="20" width="19" height="6" rx="1" />
      </g>
      <g stroke={color} strokeWidth={STROKE}>
        <path d="M 3 28 h 23" />
        <path d="M 7 28 v 3 M 22 28 v 3" />
        <path d="M 29 12 h 11" />
        <path d="M 36 8 l 4 4 l -4 4" />
      </g>
    </g>
  ),

  /** Bottles lifted out of the case they arrived in. */
  uncaser: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <Bottle x={33} y={5} dim={dim} h={14} />
      <path d="M 27 11 l -4 -4" stroke={color} strokeWidth={THIN} />
      <path d="M 23 7 l 4 0 M 23 7 l 0 4" stroke={color} strokeWidth={THIN} />
      <g stroke={color} strokeWidth={STROKE}>
        <rect x="4" y="14" width="21" height="15" rx="1.5" />
        <path d="M 14.5 14 v 15" />
        <path d="M 4 19 h 21" />
      </g>
    </g>
  ),

  /** A wash tunnel: bottles pass through under spray. */
  washer: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <rect x="3" y="13" width="38" height="15" rx="4" />
        <path d="M 11 6 v 5 M 22 6 v 5 M 33 6 v 5" />
        <path d="M 8 6 h 6 M 19 6 h 6 M 30 6 h 6" />
      </g>
      <g stroke={dim} strokeWidth={THIN}>
        <path d="M 11 16 v 3 M 22 16 v 3 M 33 16 v 3" />
        <path d="M 8 24 h 28" />
      </g>
    </g>
  ),

  /** A camera looking across the line at a bottle. */
  inspector: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <rect x="3" y="6" width="15" height="11" rx="2" />
        <path d="M 18 9 l 5 -2.5 v 10 l -5 -2.5 z" />
        <path d="M 10 17 v 4" />
      </g>
      <path d="M 24 9 l 8 4 M 24 17 l 8 -4" stroke={dim} strokeWidth={THIN} />
      <Bottle x={35} y={13} dim={dim} h={13} />
    </g>
  ),

  /** A bowl feeding filling valves, with product leaving them. */
  filler: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <path d="M 6 4 h 32 v 11 l -6 6 H 12 l -6 -6 z" />
        <path d="M 14 21 v 4 M 22 21 v 4 M 30 21 v 4" />
      </g>
      <g stroke={dim} strokeWidth={THIN}>
        <path d="M 14 27 v 2 M 22 27 v 2 M 30 27 v 2" />
        <path d="M 6 9 h 32" />
      </g>
    </g>
  ),

  /** A rotary head bringing a closure down onto a bottle. */
  capper: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <rect x="10" y="3" width="24" height="6" rx="2" />
        <path d="M 16 9 v 4 M 28 9 v 4" />
        <path d="M 22 9 v 6" />
        <path d="M 17 15 h 10" />
      </g>
      <Bottle x={22} y={17} dim={dim} h={12} />
    </g>
  ),

  /** A heat tunnel: the waves are the point. */
  pasteurizer: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="10" width="38" height="18" rx="3" stroke={color} strokeWidth={STROKE} />
      <g stroke={color} strokeWidth={THIN}>
        <path d="M 10 22 q 3 -4 6 0 t 6 0 t 6 0 t 6 0" />
        <path d="M 10 16 q 3 -4 6 0 t 6 0 t 6 0 t 6 0" />
      </g>
      <path d="M 8 6 v -3 M 22 6 v -3 M 36 6 v -3" stroke={dim} strokeWidth={THIN} />
    </g>
  ),

  /** A label reel paying off onto a bottle. */
  labeller: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <circle cx="11" cy="12" r="8" />
        <circle cx="11" cy="12" r="2.5" />
        <path d="M 19 12 h 7" />
      </g>
      <Bottle x={33} y={8} dim={dim} h={16} />
      <rect x="28" y="16" width="11" height="5" rx="1" stroke={color} strokeWidth={THIN} fill="none" />
    </g>
  ),

  /** A print head laying down a date code. */
  coder: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <rect x="8" y="3" width="17" height="10" rx="2" />
        <path d="M 16.5 13 v 5" />
        <path d="M 13 18 h 7" />
      </g>
      <g fill={dim} stroke="none">
        {[0, 1, 2, 3].map((i) => (
          <circle key={`d${i}`} cx={9 + i * 5} cy={23} r="1.1" />
        ))}
        {[0, 1, 2, 3].map((i) => (
          <circle key={`e${i}`} cx={9 + i * 5} cy={27} r="1.1" />
        ))}
      </g>
    </g>
  ),

  /** Bottles dropped into the carton that will carry them. */
  packer: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={dim} strokeWidth={THIN}>
        <path d="M 15 3 v 7 M 22 3 v 7 M 29 3 v 7" />
        <path d="M 22 10 l -3 -3 M 22 10 l 3 -3" />
      </g>
      <g stroke={color} strokeWidth={STROKE}>
        <path d="M 8 14 l -4 -4 M 36 14 l 4 -4" />
        <rect x="8" y="14" width="28" height="15" rx="1.5" />
        <path d="M 8 19 h 28" />
      </g>
    </g>
  ),

  /** Film paid off a reel and drawn over the pack. */
  shrink: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <circle cx="9" cy="9" r="6" />
        <circle cx="9" cy="9" r="2" />
        <path d="M 15 9 q 8 0 10 7" />
      </g>
      <Case x={20} y={16} w={20} h={13} dim={dim} />
    </g>
  ),

  /** A tape head closing the case along its seam. */
  sealer: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <circle cx="31" cy="8" r="6" />
        <circle cx="31" cy="8" r="2" />
        <path d="M 25 8 h -6 l -4 6" />
      </g>
      <Case x={5} y={15} w={26} h={14} dim={dim} />
      <path d="M 5 15 h 26" stroke={color} strokeWidth={STROKE} />
    </g>
  ),

  /** A weigh platform on its load cell, reading out. */
  checkweigher: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <path d="M 4 18 h 26" />
        <path d="M 17 18 v 8" />
        <path d="M 9 26 h 16" />
        <circle cx="36" cy="9" r="6" />
        <path d="M 36 9 l 3 -3" />
      </g>
      <Case x={11} y={9} w={12} h={9} dim={dim} />
    </g>
  ),

  /** An arm stacking cases onto a pallet. */
  palletizer: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={color} strokeWidth={STROKE}>
        <rect x="3" y="20" width="9" height="9" rx="1" />
        <path d="M 7.5 20 L 14 7 L 28 11" />
        <path d="M 28 11 v 4" />
      </g>
      <Case x={23} y={15} w={11} h={7} dim={dim} />
      <g stroke={dim} strokeWidth={THIN}>
        <path d="M 20 26 h 20" />
        <path d="M 23 26 v 3 M 37 26 v 3" />
      </g>
    </g>
  ),

  /** A load turning while film wraps it. */
  stretch: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round" strokeLinejoin="round">
      <g stroke={dim} strokeWidth={THIN}>
        <rect x="15" y="10" width="16" height="14" rx="1" />
        <path d="M 13 24 h 20 M 16 24 v 3 M 30 24 v 3" />
      </g>
      <g stroke={color} strokeWidth={STROKE}>
        <path d="M 11 13 q -5 4 0 8" />
        <circle cx="7" cy="17" r="3.5" />
        <path d="M 15 8 q 8 -4 16 0" />
        <path d="M 28 5 l 3 3 l -3 3" />
        <path d="M 15 20 h 16" />
      </g>
    </g>
  ),

  /** Only drawn in the legend — on the diagram a conveyor is the link itself. */
  conveyor: ({ color, dim }) => (
    <g fill="none" strokeLinecap="round">
      <g stroke={color} strokeWidth={STROKE}>
        <path d="M 3 12 h 38 M 3 24 h 38" />
      </g>
      <g stroke={dim} strokeWidth={THIN}>
        {[0, 1, 2, 3].map((i) => (
          <circle key={i} cx={9 + i * 9} cy="18" r="3" />
        ))}
      </g>
    </g>
  ),
};

export function hasSymbol(type: string): type is MachineType {
  return type in SYMBOLS;
}

/**
 * One machine, drawn as itself.
 *
 * `x`/`y` place the top-left of the symbol box. Unknown types fall back to a
 * plain outline rather than nothing, so a machine added to the registry without
 * a symbol still appears on the line instead of leaving a hole in it.
 */
export function StationSymbol({
  type,
  x,
  y,
  color,
  dim,
}: {
  type: string;
  x: number;
  y: number;
  color: string;
  dim: string;
}) {
  const Draw = hasSymbol(type) ? SYMBOLS[type] : null;
  return (
    <g transform={`translate(${x} ${y})`} aria-hidden>
      {Draw ? (
        <Draw color={color} dim={dim} />
      ) : (
        <rect
          x="4"
          y="6"
          width={SYMBOL_W - 8}
          height={SYMBOL_H - 12}
          rx="3"
          fill="none"
          stroke={color}
          strokeWidth={STROKE}
        />
      )}
    </g>
  );
}
