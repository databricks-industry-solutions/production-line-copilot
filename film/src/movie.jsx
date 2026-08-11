// @ds-adherence-ignore -- omelette starter scaffold (raw elements/hex/px by design)

/* BEGIN USAGE */
// animations.jsx
// Reusable animation starter: Stage, Timeline, Sprite, easing helpers.
// Exports (to window): Stage, Sprite, PlaybackBar, TextSprite, ImageSprite, RectSprite,
//   useTime, useTimeline, useSprite, Easing, interpolate, animate, clamp.
//
// Usage (in an HTML file that loads React + Babel):
//
//   <Stage width={1280} height={720} duration={10} background="#f6f4ef">
//     <MyScene />
//   </Stage>
//
// <Stage> auto-scales to the viewport and provides the scrubber, play/pause,
// ←/→ seek, space, and 0-to-reset controls, and persists the playhead.
// Inside <Stage>, any child can call useTime() to read the current
// playhead (seconds). Or wrap content in <Sprite start={1} end={4}>...</Sprite>
// to only render during that window -- children receive a `localTime` and
// `progress` via the useSprite() hook. Use Easing + interpolate()/animate()
// for tweens; TextSprite / ImageSprite / RectSprite have built-in entry/exit.
// Build YOUR scenes by composing Sprites inside a Stage.
/* END USAGE */
// ─────────────────────────────────────────────────────────────────────────────

// ── Easing functions (hand-rolled, Popmotion-style) ─────────────────────────
// All easings take t ∈ [0,1] and return eased t ∈ [0,1] (may overshoot for back/elastic).
const Easing = {
  linear: (t) => t,

  // Quad
  easeInQuad:    (t) => t * t,
  easeOutQuad:   (t) => t * (2 - t),
  easeInOutQuad: (t) => (t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t),

  // Cubic
  easeInCubic:    (t) => t * t * t,
  easeOutCubic:   (t) => (--t) * t * t + 1,
  easeInOutCubic: (t) => (t < 0.5 ? 4 * t * t * t : (t - 1) * (2 * t - 2) * (2 * t - 2) + 1),

  // Quart
  easeInQuart:    (t) => t * t * t * t,
  easeOutQuart:   (t) => 1 - (--t) * t * t * t,
  easeInOutQuart: (t) => (t < 0.5 ? 8 * t * t * t * t : 1 - 8 * (--t) * t * t * t),

  // Expo
  easeInExpo:  (t) => (t === 0 ? 0 : Math.pow(2, 10 * (t - 1))),
  easeOutExpo: (t) => (t === 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  easeInOutExpo: (t) => {
    if (t === 0) return 0;
    if (t === 1) return 1;
    if (t < 0.5) return 0.5 * Math.pow(2, 20 * t - 10);
    return 1 - 0.5 * Math.pow(2, -20 * t + 10);
  },

  // Sine
  easeInSine:    (t) => 1 - Math.cos((t * Math.PI) / 2),
  easeOutSine:   (t) => Math.sin((t * Math.PI) / 2),
  easeInOutSine: (t) => -(Math.cos(Math.PI * t) - 1) / 2,

  // Back (overshoot)
  easeOutBack: (t) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
  },
  easeInBack: (t) => {
    const c1 = 1.70158, c3 = c1 + 1;
    return c3 * t * t * t - c1 * t * t;
  },
  easeInOutBack: (t) => {
    const c1 = 1.70158, c2 = c1 * 1.525;
    return t < 0.5
      ? (Math.pow(2 * t, 2) * ((c2 + 1) * 2 * t - c2)) / 2
      : (Math.pow(2 * t - 2, 2) * ((c2 + 1) * (t * 2 - 2) + c2) + 2) / 2;
  },

  // Elastic
  easeOutElastic: (t) => {
    const c4 = (2 * Math.PI) / 3;
    if (t === 0) return 0;
    if (t === 1) return 1;
    return Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * c4) + 1;
  },
};

// ── Core interpolation helpers ──────────────────────────────────────────────

// Clamp a value to [min, max]
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

// interpolate([0, 0.5, 1], [0, 100, 50], ease?) -> fn(t)
// Popmotion-style: linearly maps t across input keyframes to output values,
// with optional easing per segment (single fn or array of fns).
function interpolate(input, output, ease = Easing.linear) {
  return (t) => {
    if (t <= input[0]) return output[0];
    if (t >= input[input.length - 1]) return output[output.length - 1];
    for (let i = 0; i < input.length - 1; i++) {
      if (t >= input[i] && t <= input[i + 1]) {
        const span = input[i + 1] - input[i];
        const local = span === 0 ? 0 : (t - input[i]) / span;
        const easeFn = Array.isArray(ease) ? (ease[i] || Easing.linear) : ease;
        const eased = easeFn(local);
        return output[i] + (output[i + 1] - output[i]) * eased;
      }
    }
    return output[output.length - 1];
  };
}

// animate({from, to, start, end, ease})(t) — simpler single-segment tween.
// Returns `from` before `start`, `to` after `end`.
function animate({ from = 0, to = 1, start = 0, end = 1, ease = Easing.easeInOutCubic }) {
  return (t) => {
    if (t <= start) return from;
    if (t >= end) return to;
    const local = (t - start) / (end - start);
    return from + (to - from) * ease(local);
  };
}

// ── Timeline context ────────────────────────────────────────────────────────

const TimelineContext = React.createContext({ time: 0, duration: 10, playing: false });

const useTime = () => React.useContext(TimelineContext).time;
const useTimeline = () => React.useContext(TimelineContext);

// ── Sprite ──────────────────────────────────────────────────────────────────
// Renders children only when the playhead is inside [start, end]. Provides
// a sub-context with `localTime` (seconds since start) and `progress` (0..1).
//
//   <Sprite start={2} end={5}>
//     {({ localTime, progress }) => <Thing x={progress * 100} />}
//   </Sprite>
//
// Or as a plain wrapper — children can call useSprite() themselves.

const SpriteContext = React.createContext({ localTime: 0, progress: 0, duration: 0 });
const useSprite = () => React.useContext(SpriteContext);

function Sprite({ start = 0, end = Infinity, children, keepMounted = false }) {
  const { time } = useTimeline();
  const visible = time >= start && time <= end;
  if (!visible && !keepMounted) return null;

  const duration = end - start;
  const localTime = Math.max(0, time - start);
  const progress = duration > 0 && isFinite(duration)
    ? clamp(localTime / duration, 0, 1)
    : 0;

  const value = { localTime, progress, duration, visible };

  return (
    <SpriteContext.Provider value={value}>
      {typeof children === 'function' ? children(value) : children}
    </SpriteContext.Provider>
  );
}

// ── Sample sprite components ────────────────────────────────────────────────

// TextSprite: fades/slides text in on entry, holds, then fades out on exit.
// Props: text, x, y, size, color, font, entryDur, exitDur, align
function TextSprite({
  text,
  x = 0, y = 0,
  size = 48,
  color = '#111',
  font = 'Inter, system-ui, sans-serif',
  weight = 600,
  entryDur = 0.45,
  exitDur = 0.35,
  entryEase = Easing.easeOutBack,
  exitEase = Easing.easeInCubic,
  align = 'left',
  letterSpacing = '-0.01em',
}) {
  const { localTime, duration } = useSprite();
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let ty = 0;

  if (localTime < entryDur) {
    const t = entryEase(clamp(localTime / entryDur, 0, 1));
    opacity = t;
    ty = (1 - t) * 16;
  } else if (localTime > exitStart) {
    const t = exitEase(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    ty = -t * 8;
  }

  const translateX = align === 'center' ? '-50%' : align === 'right' ? '-100%' : '0';

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      transform: `translate(${translateX}, ${ty}px)`,
      opacity,
      fontFamily: font,
      fontSize: size,
      fontWeight: weight,
      color,
      letterSpacing,
      whiteSpace: 'pre',
      lineHeight: 1.1,
      willChange: 'transform, opacity',
    }}>
      {text}
    </div>
  );
}

// ImageSprite: scales + fades in; optional Ken Burns drift during hold.
function ImageSprite({
  src,
  x = 0, y = 0,
  width = 400, height = 300,
  entryDur = 0.6,
  exitDur = 0.4,
  kenBurns = false,
  kenBurnsScale = 1.08,
  radius = 12,
  fit = 'cover',
  placeholder = null, // {label: string} for striped placeholder
}) {
  const { localTime, duration } = useSprite();
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let scale = 1;

  if (localTime < entryDur) {
    const t = Easing.easeOutCubic(clamp(localTime / entryDur, 0, 1));
    opacity = t;
    scale = 0.96 + 0.04 * t;
  } else if (localTime > exitStart) {
    const t = Easing.easeInCubic(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    scale = (kenBurns ? kenBurnsScale : 1) + 0.02 * t;
  } else if (kenBurns) {
    const holdSpan = exitStart - entryDur;
    const holdT = holdSpan > 0 ? (localTime - entryDur) / holdSpan : 0;
    scale = 1 + (kenBurnsScale - 1) * holdT;
  }

  const content = placeholder ? (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'repeating-linear-gradient(135deg, #e9e6df 0 10px, #dcd8cf 10px 20px)',
      color: '#6b6458',
      fontFamily: 'JetBrains Mono, ui-monospace, monospace',
      fontSize: 13,
      letterSpacing: '0.04em',
      textTransform: 'uppercase',
    }}>
      {placeholder.label || 'image'}
    </div>
  ) : (
    <img src={src} alt="" style={{ width: '100%', height: '100%', objectFit: fit, display: 'block' }} />
  );

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width, height,
      opacity,
      transform: `scale(${scale})`,
      transformOrigin: 'center',
      borderRadius: radius,
      overflow: 'hidden',
      willChange: 'transform, opacity',
    }}>
      {content}
    </div>
  );
}

// RectSprite: simple rectangle that animates position/size/color via props.
// Useful demo primitive — takes a `render` fn for per-frame customization.
function RectSprite({
  x = 0, y = 0,
  width = 100, height = 100,
  color = '#111',
  radius = 8,
  entryDur = 0.4,
  exitDur = 0.3,
  render, // optional: (ctx) => style overrides
}) {
  const spriteCtx = useSprite();
  const { localTime, duration } = spriteCtx;
  const exitStart = Math.max(0, duration - exitDur);

  let opacity = 1;
  let scale = 1;

  if (localTime < entryDur) {
    const t = Easing.easeOutBack(clamp(localTime / entryDur, 0, 1));
    opacity = clamp(localTime / entryDur, 0, 1);
    scale = 0.4 + 0.6 * t;
  } else if (localTime > exitStart) {
    const t = Easing.easeInQuad(clamp((localTime - exitStart) / exitDur, 0, 1));
    opacity = 1 - t;
    scale = 1 - 0.15 * t;
  }

  const overrides = render ? render(spriteCtx) : {};

  return (
    <div style={{
      position: 'absolute',
      left: x, top: y,
      width, height,
      background: color,
      borderRadius: radius,
      opacity,
      transform: `scale(${scale})`,
      transformOrigin: 'center',
      willChange: 'transform, opacity',
      ...overrides,
    }} />
  );
}


function Stage({
  width = 1280,
  height = 720,
  duration = 10,
  background = '#f6f4ef',
  fps = 60,
  loop = true,
  autoplay = true,
  persistKey = 'animstage',
  rate = 1,
  chapters = [],
  children,
}) {
  const [time, setTime] = React.useState(() => {
    try {
      const v = parseFloat(localStorage.getItem(persistKey + ':t') || '0');
      return isFinite(v) ? clamp(v, 0, duration) : 0;
    } catch { return 0; }
  });
  const [playing, setPlaying] = React.useState(autoplay);
  const [hoverTime, setHoverTime] = React.useState(null);
  const [scale, setScale] = React.useState(1);

  const stageRef = React.useRef(null);
  const canvasRef = React.useRef(null);
  const rafRef = React.useRef(null);
  const lastTsRef = React.useRef(null);

  // Persist playhead
  React.useEffect(() => {
    try { localStorage.setItem(persistKey + ':t', String(time)); } catch {}
  }, [time, persistKey]);

  // Dev seek hook (for inspection/screenshots)
  React.useEffect(() => {
    window.__stage = { setTime, setPlaying, duration, chapters };
    return () => { if (window.__stage && window.__stage.setTime === setTime) delete window.__stage; };
  }, [duration, chapters]);

  // Auto-scale to fit viewport
  React.useEffect(() => {
    if (!stageRef.current) return;
    const el = stageRef.current;
    const measure = () => {
      const barH = 44; // playback bar height
      const s = Math.min(
        el.clientWidth / width,
        (el.clientHeight - barH) / height
      );
      setScale(Math.max(0.05, s));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [width, height]);

  // Animation loop
  React.useEffect(() => {
    if (!playing) {
      lastTsRef.current = null;
      return;
    }
    const step = (ts) => {
      if (lastTsRef.current == null) lastTsRef.current = ts;
      const dt = ((ts - lastTsRef.current) / 1000) * rate;
      lastTsRef.current = ts;
      setTime((t) => {
        let next = t + dt;
        if (next >= duration) {
          if (loop) next = next % duration;
          else { next = duration; setPlaying(false); }
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      lastTsRef.current = null;
    };
  }, [playing, duration, loop, rate]);

  // Keyboard: space = play/pause, ← → = fine scrub, J/L = 5s jump
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
      if (e.code === 'Space') {
        e.preventDefault();
        setPlaying(p => !p);
      } else if (e.code === 'ArrowLeft') {
        setTime(t => clamp(t - (e.shiftKey ? 1 : 0.1), 0, duration));
      } else if (e.code === 'ArrowRight') {
        setTime(t => clamp(t + (e.shiftKey ? 1 : 0.1), 0, duration));
      } else if (e.code === 'KeyJ') {
        setTime(t => clamp(t - 5, 0, duration));
      } else if (e.code === 'KeyL') {
        setTime(t => clamp(t + 5, 0, duration));
      } else if (e.key === '0' || e.code === 'Home') {
        setTime(0);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [duration]);

  const displayTime = hoverTime != null ? hoverTime : time;

  const ctxValue = React.useMemo(
    () => ({ time: displayTime, duration, playing, setTime, setPlaying }),
    [displayTime, duration, playing]
  );

  return (
    <div
      ref={stageRef}
      style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center',
        background: '#0a0a0a',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      {/* Canvas area — vertically centered in remaining space */}
      <div style={{
        flex: 1,
        width: '100%',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        overflow: 'hidden',
        minHeight: 0,
      }}>
        <div
          ref={canvasRef}
          style={{
            width, height,
            background,
            position: 'relative',
            transform: `scale(${scale})`,
            transformOrigin: 'center',
            flexShrink: 0,
            boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
            overflow: 'hidden',
          }}
        >
          <TimelineContext.Provider value={ctxValue}>
            {children}
          </TimelineContext.Provider>
        </div>
      </div>

      {/* Playback bar — stacked below canvas, never overlapping */}
      <PlaybackBar
        time={displayTime}
        actualTime={time}
        duration={duration}
        playing={playing}
        chapters={chapters}
        onPlayPause={() => setPlaying(p => !p)}
        onReset={() => { setTime(0); }}
        onSeek={(t) => setTime(t)}
        onSkip={(d) => setTime((t) => clamp(t + d, 0, duration))}
        onHover={(t) => setHoverTime(t)}
      />
    </div>
  );
}

// ── Playback bar ────────────────────────────────────────────────────────────
// Play/pause, return-to-begin, scrub track, time display.
// Uses fixed-width time fields so layout doesn't thrash.

function PlaybackBar({ time, duration, playing, onPlayPause, onReset, onSeek, onSkip, onHover, chapters = [] }) {
  const trackRef = React.useRef(null);
  const [dragging, setDragging] = React.useState(false);
  const [agendaOpen, setAgendaOpen] = React.useState(false);

  const timeFromEvent = React.useCallback((e) => {
    const rect = trackRef.current.getBoundingClientRect();
    const x = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    return x * duration;
  }, [duration]);

  const onTrackMove = (e) => {
    if (!trackRef.current) return;
    const t = timeFromEvent(e);
    if (dragging) {
      onSeek(t);
    } else {
      onHover(t);
    }
  };

  const onTrackLeave = () => {
    if (!dragging) onHover(null);
  };

  const onTrackDown = (e) => {
    setDragging(true);
    const t = timeFromEvent(e);
    onSeek(t);
    onHover(null);
  };

  React.useEffect(() => {
    if (!dragging) return;
    const onUp = () => setDragging(false);
    const onMove = (e) => {
      if (!trackRef.current) return;
      const t = timeFromEvent(e);
      onSeek(t);
    };
    window.addEventListener('mouseup', onUp);
    window.addEventListener('mousemove', onMove);
    return () => {
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('mousemove', onMove);
    };
  }, [dragging, timeFromEvent, onSeek]);

  const pct = duration > 0 ? (time / duration) * 100 : 0;
  const fmt = (t) => {
    const total = Math.max(0, t);
    const m = Math.floor(total / 60);
    const s = Math.floor(total % 60);
    const cs = Math.floor((total * 100) % 100);
    return `${String(m).padStart(1, '0')}:${String(s).padStart(2, '0')}.${String(cs).padStart(2, '0')}`;
  };

  const mono = 'JetBrains Mono, ui-monospace, SFMono-Regular, monospace';

  // Chapters, in time order whatever order they were declared in, plus the one
  // the playhead is inside. Both are derived rather than stored so seeking —
  // by click, drag, arrow key or agenda — cannot leave the label out of step.
  const marks = React.useMemo(
    () => chapters.slice().sort((a, b) => a.start - b.start),
    [chapters]
  );
  const currentIndex = React.useMemo(() => {
    let found = -1;
    for (let i = 0; i < marks.length; i += 1) {
      if (time >= marks[i].start - 1e-6) found = i;
    }
    return found;
  }, [marks, time]);
  const current = currentIndex >= 0 ? marks[currentIndex] : null;
  const numbered = marks.some((c) => c.number != null);

  const jumpTo = (chapter) => {
    onSeek(chapter.start + 0.001);
    onHover(null);
    setAgendaOpen(false);
  };

  return (
    <div style={{
      position: 'relative',
      width: '100%',
      maxWidth: 680,
      alignSelf: 'center',
      flexShrink: 0,
    }}>
    {agendaOpen && marks.length > 0 && (
      <div style={{
        position: 'absolute',
        bottom: '100%', left: 0, right: 0,
        marginBottom: 8,
        maxHeight: 320,
        overflowY: 'auto',
        background: 'rgba(20,20,20,0.97)',
        border: '1px solid rgba(255,255,255,0.10)',
        borderRadius: 8,
        padding: 6,
        boxShadow: '0 -8px 32px rgba(0,0,0,0.45)',
        color: '#f6f4ef',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}>
        {marks.map((chapter, i) => (
          <button
            key={chapter.id}
            onClick={() => jumpTo(chapter)}
            style={{
              display: 'flex', alignItems: 'center', gap: 10,
              width: '100%',
              padding: '7px 10px',
              background: i === currentIndex ? 'rgba(255,255,255,0.10)' : 'transparent',
              border: 'none',
              borderRadius: 6,
              cursor: 'pointer',
              textAlign: 'left',
              color: 'inherit',
              font: 'inherit',
            }}
          >
            <span style={{
              fontFamily: mono, fontSize: 11,
              fontVariantNumeric: 'tabular-nums',
              color: 'rgba(246,244,239,0.5)',
              width: 40, flexShrink: 0,
            }}>
              {fmt(chapter.start).slice(0, -3)}
            </span>
            {numbered && (
              <span style={{
                fontFamily: mono, fontSize: 11,
                fontVariantNumeric: 'tabular-nums',
                color: chapter.number ? 'rgba(246,244,239,0.75)' : 'transparent',
                width: 16, flexShrink: 0, textAlign: 'right',
              }}>
                {chapter.number ?? '0'}
              </span>
            )}
            <span style={{ fontSize: 12.5, flex: 1 }}>{chapter.title}</span>
            {chapter.status === 'roadmap' && (
              <span style={{
                fontFamily: mono, fontSize: 9,
                letterSpacing: '.08em',
                color: '#ffb020',
                border: '1px solid rgba(255,176,32,0.45)',
                borderRadius: 3,
                padding: '1px 5px',
                flexShrink: 0,
              }}>
                ROADMAP
              </span>
            )}
          </button>
        ))}
      </div>
    )}
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '8px 16px',
      background: 'rgba(20,20,20,0.92)',
      borderTop: '1px solid rgba(255,255,255,0.08)',
      width: '100%',

      borderRadius: 8,
      color: '#f6f4ef',
      fontFamily: 'Inter, system-ui, sans-serif',
      userSelect: 'none',
    }}>
      <IconButton onClick={onReset} title="Return to start (0)">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M3 2v10M12 2L5 7l7 5V2z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round"/>
        </svg>
      </IconButton>
      <IconButton onClick={() => onSkip(-5)} title="Back 5 seconds (J)">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M12 3v8L6 7l6-4z" fill="currentColor"/>
          <rect x="2" y="3" width="2" height="8" fill="currentColor"/>
        </svg>
      </IconButton>
      <IconButton onClick={onPlayPause} title="Play/pause (space)">
        {playing ? (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="3" y="2" width="3" height="10" fill="currentColor"/>
            <rect x="8" y="2" width="3" height="10" fill="currentColor"/>
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M3 2l9 5-9 5V2z" fill="currentColor"/>
          </svg>
        )}
      </IconButton>

      <IconButton onClick={() => onSkip(5)} title="Forward 5 seconds (L)">
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <path d="M2 3v8l6-4-6-4z" fill="currentColor"/>
          <rect x="10" y="3" width="2" height="8" fill="currentColor"/>
        </svg>
      </IconButton>

      {/* Current time: fixed width so it doesn't thrash */}
      <div style={{
        fontFamily: mono,
        fontSize: 12,
        fontVariantNumeric: 'tabular-nums',
        width: 64, textAlign: 'right',
        color: '#f6f4ef',
      }}>
        {fmt(time)}
      </div>

      {/* Scrub track */}
      <div
        ref={trackRef}
        onMouseMove={onTrackMove}
        onMouseLeave={onTrackLeave}
        onMouseDown={onTrackDown}
        style={{
          flex: 1,
          height: 22,
          position: 'relative',
          cursor: 'pointer',
          display: 'flex', alignItems: 'center',
        }}
      >
        <div style={{
          position: 'absolute',
          left: 0, right: 0, height: 4,
          background: 'rgba(255,255,255,0.12)',
          borderRadius: 2,
        }}/>
        <div style={{
          position: 'absolute',
          left: 0, width: `${pct}%`, height: 4,
          background: 'oklch(72% 0.12 250)',
          borderRadius: 2,
        }}/>
        {/* Chapter boundaries, so the shape of the film is legible from the
            track itself rather than only from the open agenda. */}
        {marks.map((chapter) => (
          <div
            key={chapter.id}
            style={{
              position: 'absolute',
              left: `${duration > 0 ? (chapter.start / duration) * 100 : 0}%`,
              top: '50%',
              width: 2, height: 10,
              marginTop: -5,
              background: chapter.status === 'roadmap' ? 'rgba(255,176,32,0.85)' : 'rgba(255,255,255,0.45)',
              pointerEvents: 'none',
            }}
          />
        ))}
        <div style={{
          position: 'absolute',
          left: `${pct}%`, top: '50%',
          width: 12, height: 12,
          marginLeft: -6, marginTop: -6,
          background: '#fff',
          borderRadius: 6,
          boxShadow: '0 2px 4px rgba(0,0,0,0.4)',
        }}/>
      </div>

      {/* Duration: fixed width */}
      <div style={{
        fontFamily: mono,
        fontSize: 12,
        fontVariantNumeric: 'tabular-nums',
        width: 64, textAlign: 'left',
        color: 'rgba(246,244,239,0.55)',
      }}>
        {fmt(duration)}
      </div>

      {marks.length > 0 && (
        <button
          onClick={() => setAgendaOpen((open) => !open)}
          title="Chapters"
          style={{
            display: 'flex', alignItems: 'center', gap: 7,
            maxWidth: 210,
            padding: '4px 9px',
            background: agendaOpen ? 'rgba(255,255,255,0.12)' : 'transparent',
            border: '1px solid rgba(255,255,255,0.14)',
            borderRadius: 6,
            color: '#f6f4ef',
            cursor: 'pointer',
            fontFamily: 'inherit',
            flexShrink: 0,
          }}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" style={{ flexShrink: 0 }}>
            <path d="M1 2h10M1 6h10M1 10h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <span style={{
            fontSize: 11.5,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {current
              ? (current.number ? `${current.number} · ${current.title}` : current.title)
              : 'Chapters'}
          </span>
        </button>
      )}
    </div>
    </div>
  );
}

function IconButton({ children, onClick, title }) {
  const [hover, setHover] = React.useState(false);
  return (
    <button
      onClick={onClick}
      title={title}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: 28, height: 28,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: hover ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.04)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 6,
        color: '#f6f4ef',
        cursor: 'pointer',
        padding: 0,
        transition: 'background 120ms',
      }}
    >
      {children}
    </button>
  );
}


// ═════════════════════════════════════════════════════════════════════════
//  PRODLINE COPILOT — "A day on the line" (90s brand film)
// ═════════════════════════════════════════════════════════════════════════

const C = {
  oat:'#F9F7F4', oatMed:'#EEEDE9', navy:'#1B3139', inkDeep:'#0B2026',
  navy700:'#1B5162', lava:'#FF3621', coral:'#FF5F46', ink:'#3A3838',
  inkSoft:'#5A5755', inkMuted:'#8A8580', green:'#00A972', amber:'#FFAB00',
  slate:'#618794', line:'#E4E2DD', white:'#ffffff', tintCoral:'#FF9E94',
  glass:'#0e171b',
};
const SANS = "'DM Sans', system-ui, -apple-system, sans-serif";
const MONO = "'DM Mono', ui-monospace, monospace";
const A = (p) => p; // path helper for assets (relative)

// ── tweakable config (set from Movie props) ──────────────────────────────────
const ACCENTS = {
  lava:  { lava: '#FF3621', coral: '#FF5F46', tintCoral: '#FF9E94' },
  navy:  { lava: '#1B5162', coral: '#2E6B7E', tintCoral: '#7FA8B5' },
  green: { lava: '#00A972', coral: '#34C08C', tintCoral: '#8FD9BF' },
  amber: { lava: '#E59400', coral: '#FFAB00', tintCoral: '#FFD27A' },
};
const CFG = { name: 'Maya', showClock: true };

/**
 * The agenda: what the film covers, and where each part starts.
 *
 * Declared once and used three ways — tick marks on the scrub track, the label
 * on the chapters button, and the clickable list that seeks. Each `start` is
 * the beginning of the scene that chapter names, so a jump lands on the scene
 * opening rather than a second into it.
 *
 * `status: 'roadmap'` marks capability the film shows but the product does not
 * yet have. An animation can draw anything, which is exactly why this has to be
 * explicit: the chapter carries a badge here, the scene carries one on screen,
 * and the narration says the word out loud. The film already did this once, for
 * the MCP write-back panel. A test pins the convention so a roadmap chapter
 * cannot quietly lose its badge.
 */
const CHAPTERS = [
  { id: 'line', start: 0, title: 'The line, and the shift target', status: 'shipped' },
  { id: 'systems', start: 9, title: 'Answers in four different systems', status: 'shipped' },
  { id: 'tower', start: 22, title: 'One live view of the whole line', status: 'shipped' },
  { id: 'incident', start: 36, title: 'Filler #3 stops', status: 'shipped' },
  { id: 'ask', start: 50, title: 'Ask, and the right specialist answers', status: 'shipped' },
  { id: 'recover', start: 94.3, title: 'The line recovers', status: 'shipped' },
  { id: 'close', start: 102, title: 'What it is', status: 'shipped' },
];
function applyAccent(key) {
  const a = ACCENTS[key] || ACCENTS.lava;
  C.lava = a.lava; C.coral = a.coral; C.tintCoral = a.tintCoral;
}

// ── tiny utilities ──────────────────────────────────────────────────────────
function rev(lt, at, dur = 0.5, ease = Easing.easeOutCubic) {
  return ease(clamp((lt - at) / dur, 0, 1));
}
function Reveal({ at = 0, dur = 0.5, from = 'up', dist = 18, lt = 0, children, style = {}, ease = Easing.easeOutCubic }) {
  const e = rev(lt, at, dur, ease);
  const ty = from === 'up' ? (1 - e) * dist : from === 'down' ? -(1 - e) * dist : 0;
  const tx = from === 'left' ? (1 - e) * dist : from === 'right' ? -(1 - e) * dist : 0;
  return <div style={{ opacity: e, transform: `translate(${tx}px,${ty}px)`, ...style }}>{children}</div>;
}
function Scene({ start, end, fade = 0.6, children }) {
  return (
    <Sprite start={start} end={end}>
      {({ localTime, duration }) => {
        const o = Math.min(clamp(localTime / fade, 0, 1), clamp((duration - localTime) / fade, 0, 1));
        return (
          <div style={{ position: 'absolute', inset: 0, opacity: o }}>
            {typeof children === 'function' ? children(localTime) : children}
          </div>
        );
      }}
    </Sprite>
  );
}
function Zoom({ scale = 1, x = 0, y = 0, origin = 'center', children }) {
  return (
    <div style={{ position: 'absolute', inset: 0, transform: `translate(${x}px,${y}px) scale(${scale})`, transformOrigin: origin, willChange: 'transform' }}>
      {children}
    </div>
  );
}

// ── glyphs (lucide-ish, monoline) ────────────────────────────────────────────
function Glyph({ kind, size = 24, color = C.navy, sw = 2 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: color, strokeWidth: sw, strokeLinecap: 'round', strokeLinejoin: 'round' };
  switch (kind) {
    case 'sheet': return <svg {...common}><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>;
    case 'radio': return <svg {...common}><rect x="7" y="9" width="13" height="11" rx="2"/><path d="M7 9 16 4M11 14h.01"/><circle cx="15.5" cy="14.5" r="2"/><path d="M4 12v8"/></svg>;
    case 'clipboard': return <svg {...common}><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4h6v3H9z M9 11h6M9 15h4"/></svg>;
    case 'bell': return <svg {...common}><path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 19a2 2 0 0 0 4 0"/></svg>;
    case 'chart': return <svg {...common}><path d="M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-7"/></svg>;
    case 'phone': return <svg {...common}><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>;
    case 'send': return <svg {...common}><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/></svg>;
    case 'check': return <svg {...common}><path d="M20 6 9 17l-5-5"/></svg>;
    case 'bolt': return <svg {...common}><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>;
    case 'cursor': return <svg width={size} height={size} viewBox="0 0 24 24" fill={color} stroke="#fff" strokeWidth="1.4" strokeLinejoin="round"><path d="M5 3l14 7-6 1.5L9.5 18z"/></svg>;
    case 'clock': return <svg {...common}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>;
    case 'spark': return <svg {...common}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/></svg>;
    default: return null;
  }
}

// ── beer bottle ───────────────────────────────────────────────────────────────
function Bottle({ x, y, dim = false }) {
  const op = dim ? 0.4 : 1;
  return (
    <div style={{ position: 'absolute', left: x, top: y, width: 22, height: 50, opacity: op }}>
      <svg width="22" height="50" viewBox="0 0 22 50">
        <rect x="8" y="0" width="6" height="5" rx="1" fill="#7d5a32"/>
        <path d="M7 5h8v5q5 2 5 9v27a3 3 0 0 1-3 3H5a3 3 0 0 1-3-3V19q0-7 5-9z" fill="#C07A1E"/>
        <path d="M7 5h3v5q-3 1.4-4 4.5V9q0-2.6 1-4z" fill="#E0A34A" opacity="0.7"/>
        <rect x="4" y="30" width="14" height="11" rx="1.5" fill="#F4EFE6"/>
        <rect x="6.5" y="33.5" width="9" height="1.6" rx="0.8" fill={C.coral}/>
        <rect x="6.5" y="36.5" width="6" height="1.4" rx="0.7" fill={C.navy} opacity="0.55"/>
      </svg>
    </div>
  );
}

// ── machine gantry ─────────────────────────────────────────────────────────────
const STATUS_COLOR = { run: C.green, stop: C.lava, idle: C.inkMuted };
function Machine({ cx, label, status = 'run', t = 0 }) {
  const w = 158, boxH = 104, topY = 506, beltY = 716;
  const sc = STATUS_COLOR[status];
  const pulsing = status === 'run' || status === 'stop';
  const pulse = pulsing ? 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(t * (status === 'stop' ? 7 : 3))) : 1;
  return (
    <div style={{ position: 'absolute', left: cx - w / 2, top: topY, width: w }}>
      {/* legs */}
      <div style={{ position: 'absolute', left: 10, top: boxH, width: 10, height: beltY - topY - boxH, background: C.navy700, opacity: 0.9 }}/>
      <div style={{ position: 'absolute', right: 10, top: boxH, width: 10, height: beltY - topY - boxH, background: C.navy700, opacity: 0.9 }}/>
      {/* head box */}
      <div style={{ position: 'absolute', left: 0, top: 0, width: w, height: boxH, background: `linear-gradient(180deg, ${C.navy700}, ${C.navy})`, borderRadius: 10, boxShadow: '0 10px 22px rgba(11,32,38,.22)', border: `1px solid rgba(255,255,255,.06)` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '12px 14px' }}>
          <span style={{ width: 11, height: 11, borderRadius: 6, background: sc, boxShadow: `0 0 ${10 * pulse}px ${sc}`, opacity: pulse }}/>
          <span style={{ fontFamily: MONO, fontSize: 13, letterSpacing: '0.04em', color: 'rgba(255,255,255,.62)', textTransform: 'uppercase' }}>{status}</span>
        </div>
        <div style={{ padding: '0 14px', fontFamily: SANS, fontWeight: 700, fontSize: 19, color: C.white, letterSpacing: '-0.01em' }}>{label}</div>
        {/* port window */}
        <div style={{ position: 'absolute', bottom: 12, left: 14, right: 14, height: 14, borderRadius: 4, background: 'rgba(255,255,255,.08)' }}/>
      </div>
    </div>
  );
}

// ── brewery packaging line ────────────────────────────────────────────────────
const MACHINES = [
  { cx: 470, label: 'Depalletizer' },
  { cx: 770, label: 'Filler #3' },
  { cx: 1070, label: 'Capper' },
  { cx: 1370, label: 'Labeler' },
  { cx: 1670, label: 'Case Packer' },
];
function BreweryLine({ flow = 0, statuses, alert = false, t = 0, dim = false }) {
  const beltY = 716, beltX0 = 120, beltX1 = 1800, span = beltX1 - beltX0;
  const st = statuses || ['run', 'run', 'run', 'run', 'run'];
  const N = 17, spacing = (span + 120) / N;
  const bottles = [];
  for (let i = 0; i < N; i++) {
    let bx = beltX0 + ((i * spacing + flow) % (span + 120));
    if (bx > beltX1 + 10) continue;
    bottles.push(<Bottle key={i} x={bx} y={beltY - 50} dim={dim} />);
  }
  return (
    <div style={{ position: 'absolute', inset: 0 }}>
      {/* room */}
      <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(180deg, ${C.oatMed} 0%, ${C.oat} 46%, #EFEBE3 100%)` }}/>
      {/* back wall pipes / structure */}
      <div style={{ position: 'absolute', top: 150, left: 0, right: 0, height: 8, background: 'rgba(27,49,57,.05)' }}/>
      <div style={{ position: 'absolute', top: 250, left: 0, right: 0, height: 6, background: 'rgba(27,49,57,.04)' }}/>
      {/* floor */}
      <div style={{ position: 'absolute', left: 0, right: 0, top: 800, height: 280, background: `linear-gradient(180deg, #E7E2D8, #DAD4C8)` }}/>
      <div style={{ position: 'absolute', left: 0, right: 0, top: 800, height: 2, background: 'rgba(27,49,57,.10)' }}/>
      {/* conveyor frame */}
      <div style={{ position: 'absolute', left: beltX0 - 16, top: beltY + 4, width: span + 32, height: 14, background: C.navy, borderRadius: 4, opacity: 0.9 }}/>
      {/* belt */}
      <div style={{ position: 'absolute', left: beltX0, top: beltY - 6, width: span, height: 16, background: '#2b3a40', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, backgroundImage: 'repeating-linear-gradient(90deg, rgba(255,255,255,.10) 0 3px, transparent 3px 26px)', transform: `translateX(${-(flow % 26)}px)` }}/>
      </div>
      {/* belt legs */}
      {[260, 640, 1020, 1400, 1740].map((lx, i) => (
        <div key={i} style={{ position: 'absolute', left: lx, top: beltY + 18, width: 12, height: 84, background: C.navy700, opacity: 0.8, borderRadius: 2 }}/>
      ))}
      {/* bottles */}
      {bottles}
      {/* machines */}
      {MACHINES.map((m, i) => <Machine key={i} cx={m.cx} label={m.label} status={st[i]} t={t} />)}
      {dim && <div style={{ position: 'absolute', inset: 0, background: 'rgba(11,32,38,.55)' }}/>}
      {alert && (
        <div style={{ position: 'absolute', inset: 0, background: `radial-gradient(900px 600px at 770px 560px, rgba(255,54,33,${0.16 + 0.10 * (0.5 + 0.5 * Math.sin(t * 6))}), transparent 70%)` }}/>
      )}
    </div>
  );
}

// ── Maya, line supervisor ───────────────────────────────────────────────────────
function Maya({ x, y, scale = 1, calm = false }) {
  return (
    <div style={{ position: 'absolute', left: x, top: y, width: 150 * scale, transform: `scale(${1})`, transformOrigin: 'bottom center' }}>
      <svg width={150 * scale} height={210 * scale} viewBox="0 0 150 210">
        {/* shadow */}
        <ellipse cx="75" cy="206" rx="46" ry="7" fill="rgba(27,49,57,.18)"/>
        {/* body / hi-vis vest */}
        <path d="M34 210 L40 118 Q75 104 110 118 L116 210 Z" fill={C.navy}/>
        <path d="M58 210 L60 120 Q75 116 90 120 L92 210 Z" fill={calm ? C.green : C.coral} opacity="0.92"/>
        <rect x="40" y="150" width="70" height="9" fill={C.amber} opacity="0.9"/>
        {/* arms */}
        <path d="M40 120 Q24 150 30 184" stroke={C.navy} strokeWidth="16" fill="none" strokeLinecap="round"/>
        <path d="M110 120 Q126 150 120 184" stroke={C.navy} strokeWidth="16" fill="none" strokeLinecap="round"/>
        {/* neck + head */}
        <rect x="67" y="96" width="16" height="18" rx="6" fill="#E3A87E"/>
        <circle cx="75" cy="80" r="23" fill="#EFBE97"/>
        {/* hair */}
        <path d="M52 78 Q53 55 75 54 Q97 55 98 78 Q92 70 75 70 Q58 70 52 78z" fill="#4A3526"/>
        {/* hard hat */}
        <path d="M50 70 a25 24 0 0 1 50 0 z" fill={calm ? C.green : C.coral}/>
        <rect x="45" y="68" width="60" height="7" rx="3.5" fill={calm ? C.green : C.coral}/>
        <rect x="71" y="49" width="8" height="9" rx="2" fill={calm ? C.green : C.coral}/>
      </svg>
    </div>
  );
}

// ── chaos icon (orbiting overload) ───────────────────────────────────────────────
function ChaosIcon({ kind, label, cx, cy, baseAngle, rx, ry, lt, speed = 0.5, scatter = 0 }) {
  const ang = baseAngle + lt * speed;
  const jitter = Math.sin(lt * 4 + baseAngle) * 4;
  const sx = cx + Math.cos(ang) * (rx + scatter * 420) + jitter;
  const sy = cy + Math.sin(ang) * (ry + scatter * 240) + Math.cos(lt * 3 + baseAngle) * 3;
  const op = 1 - scatter;
  return (
    <div style={{ position: 'absolute', left: sx, top: sy, opacity: op, transform: `translate(-50%,-50%) rotate(${jitter * 0.4}deg)` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, width: 'max-content', maxWidth: 280, background: C.white, border: `1px solid ${C.line}`, borderRadius: 10, padding: '9px 14px', boxShadow: '0 8px 20px rgba(27,49,57,.14)' }}>
        <Glyph kind={kind} size={20} color={C.navy700} />
        <span style={{ fontFamily: SANS, fontWeight: 600, fontSize: 14, color: C.ink, whiteSpace: 'nowrap' }}>{label}</span>
      </div>
    </div>
  );
}

// ── eyebrow + caption ─────────────────────────────────────────────────────────────
function Eyebrow({ children, color = C.lava }) {
  return <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 16, letterSpacing: '0.16em', textTransform: 'uppercase', color }}>{children}</div>;
}
function Clock({ time, color = C.navy }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <Glyph kind="clock" size={20} color={color} sw={2} />
      <span style={{ fontFamily: MONO, fontSize: 20, fontWeight: 500, color, letterSpacing: '0.02em' }}>{time}</span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
//  COPILOT UI (hero, shown on the Surface tablet)
// ─────────────────────────────────────────────────────────────────────────────
const SPECIALISTS = [
  { name: 'Orchestrator', at: 0.2, sub: 'loads live line state' },
  { name: 'Downtime Analyst', at: 6.2, sub: 'root cause + cascade' },
  { name: 'Schedule Optimizer', at: 6.7, sub: 'MILP recovery plan' },
  { name: 'OEE Coach', at: 7.2, sub: 'loss decomposition' },
  { name: 'Quality Specialist', at: 7.7, sub: 'Bayesian risk model' },
  { name: 'Supply Chain Advisor', at: 8.2, sub: 'depletion + reorder' },
  { name: 'Maintenance Predictor', at: 8.7, sub: 'RUL on Filler #3' },
  { name: 'Strategic Advisor', at: 9.2, sub: 'multi-shift plan' },
  { name: 'Shift Briefing', at: 9.7, sub: 'handover summary' },
];
const STATE_CHIPS = [
  { k: 'Filler #3', v: 'DOWN · 14 min', tone: 'bad', at: 3.2 },
  { k: 'Line OEE', v: '71%', tone: 'warn', at: 3.5 },
  { k: 'Shift target', v: '8,400 cases', tone: 'neutral', at: 3.8 },
  { k: 'Shift ends', v: '14:00', tone: 'neutral', at: 4.1 },
];
const ACTIONS = [
  { t: 'Raise line speed → 105%', d: 'Labeling + packing cells, within limits', at: 21.0 },
  { t: 'Authorize +2 h overtime', d: 'Packing crew only', at: 22.0 },
  { t: 'Delay CIP → 22:00', d: 'Stays inside sanitation window', at: 23.0 },
];

function Bubble({ children, color = C.navy, bg = C.oatMed, align = 'left', maxW = 760 }) {
  return (
    <div style={{ display: 'flex', justifyContent: align === 'right' ? 'flex-end' : 'flex-start' }}>
      <div style={{ maxWidth: maxW, background: bg, color, borderRadius: align === 'right' ? '16px 16px 4px 16px' : '16px 16px 16px 4px', padding: '16px 20px', fontFamily: SANS, fontSize: 25, fontWeight: 500, lineHeight: 1.32, boxShadow: '0 2px 8px rgba(27,49,57,.06)' }}>
        {children}
      </div>
    </div>
  );
}
function StateChip({ chip, lt }) {
  const tone = chip.tone;
  const col = tone === 'bad' ? C.lava : tone === 'warn' ? C.amber : C.navy700;
  return (
    <Reveal lt={lt} at={chip.at} dur={0.4} from="up" dist={10}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, background: C.white, border: `1px solid ${C.line}`, borderLeft: `4px solid ${col}`, borderRadius: 8, padding: '10px 16px', minWidth: 150 }}>
        <span style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, color: C.inkMuted, letterSpacing: '0.02em' }}>{chip.k}</span>
        <span style={{ fontFamily: MONO, fontSize: 20, fontWeight: 500, color: col }}>{chip.v}</span>
      </div>
    </Reveal>
  );
}
function SpecialistChip({ s, lt }) {
  const active = lt >= s.at;
  const warm = clamp((lt - s.at) / 0.6, 0, 1);
  const shimmer = active && lt < s.at + 2.2;
  const glow = shimmer ? 0.5 + 0.5 * Math.sin((lt - s.at) * 6) : 0;
  return (
    <Reveal lt={lt} at={s.at} dur={0.4} from="up" dist={10}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: C.navy, borderRadius: 10, padding: '11px 15px', border: `1px solid ${C.navy700}`, boxShadow: glow ? `0 0 ${18 * glow}px rgba(255,95,70,.5)` : 'none' }}>
        <span style={{ width: 9, height: 9, borderRadius: 5, background: lt > s.at + 2 ? C.green : C.coral, opacity: 0.6 + 0.4 * (shimmer ? glow : 1) }}/>
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 17, color: C.white }}>{s.name}</span>
          <span style={{ fontFamily: SANS, fontSize: 13, color: 'rgba(255,255,255,.55)' }}>{s.sub}</span>
        </div>
      </div>
    </Reveal>
  );
}

function CopilotUI({ lt }) {
  const Q = 'Can we still hit the shift target after Filler #3 stopped?';
  const typed = Q.slice(0, Math.floor(clamp((lt - 0.3) / 2.0, 0, 1) * Q.length));
  const showSnap = lt > 2.9;
  const showSpec = lt > 5.9;
  const showRun = lt > 10.2;
  const runP = clamp((lt - 10.5) / 4.2, 0, 1);
  const showPlan = lt > 19.8;
  const tap = lt > 35.0;
  const approved = lt > 35.6;

  // feed auto-scroll
  const scroll = interpolate([0, 6, 10, 16, 20, 31, 36], [0, 30, 170, 320, 500, 620, 620], Easing.easeInOutCubic)(lt);

  return (
    <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: C.oat }}>
      {/* header */}
      <div style={{ height: 78, flexShrink: 0, background: C.navy, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 28px', borderBottom: `3px solid ${C.lava}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQxIiBoZWlnaHQ9IjI2NiIgdmlld0JveD0iMCAwIDI0MSAyNjYiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik0yMjguMDg1IDEwOS42NTRMMTIwLjYxNSAxNzEuNjc0TDUuNTM0OTMgMTA1LjQxTDAgMTA4LjQ3NVYxNTYuNTgyTDEyMC42MTUgMjI1LjkxMUwyMjguMDg1IDE2NC4xMjhWMTg5LjU5NkwxMjAuNjE1IDI1MS42MTVMNS41MzQ5MyAxODUuMzUxTDAgMTg4LjQxN1YxOTYuNjdMMTIwLjYxNSAyNjZMMjQxIDE5Ni42N1YxNDguNTY0TDIzNS40NjUgMTQ1LjQ5OEwxMjAuNjE1IDIxMS41MjdMMTIuOTE0OCAxNDkuNzQzVjEyNC4yNzVMMTIwLjYxNSAxODYuMDU5TDI0MSAxMTYuNzI5VjY5LjMyOThMMjM1LjAwNCA2NS43OTI1TDEyMC42MTUgMTMxLjU4NUwxOC40NDk4IDczLjEwMjhMMTIwLjYxNSAxNC4zODQ4TDIwNC41NjIgNjIuNzI2OUwyMTEuOTQyIDU4LjQ4MjNWNTIuNTg2OUwxMjAuNjE1IDBMMCA2OS4zMjk4Vjc2Ljg3NTlMMTIwLjYxNSAxNDYuMjA2TDIyOC4wODUgODQuMTg2MlYxMDkuNjU0WiIgZmlsbD0id2hpdGUiPjwvcGF0aD4KPC9zdmc+" style={{ width: 30, height: 30 }} />
          <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 24, color: C.white, letterSpacing: '-0.01em' }}>ProdLine Copilot</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ width: 9, height: 9, borderRadius: 5, background: C.green, boxShadow: `0 0 8px ${C.green}` }}/>
          <span style={{ fontFamily: MONO, fontSize: 16, color: 'rgba(255,255,255,.7)' }}>Line 4 · Shift A · live</span>
        </div>
      </div>
      {/* feed */}
      <div style={{ position: 'relative', flex: 1, overflow: 'hidden' }}>
        <div style={{ position: 'absolute', left: 0, right: 0, top: 24, padding: '0 30px', display: 'flex', flexDirection: 'column', gap: 18, transform: `translateY(${-scroll}px)` }}>
          {/* user question */}
          <Bubble align="right" bg={C.navy700} color={C.white}>
            {typed}<span style={{ opacity: lt < 2.4 ? (0.4 + 0.6 * Math.sin(lt * 14) ** 2) : 0 }}>▋</span>
          </Bubble>

          {/* orchestrator snapshot */}
          {showSnap && (
            <Reveal lt={lt} at={3.0} dur={0.4} from="up">
              <div style={{ background: C.white, border: `1px solid ${C.line}`, borderRadius: 14, padding: 18, boxShadow: '0 2px 10px rgba(27,49,57,.06)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                  <Glyph kind="bolt" size={20} color={C.lava} />
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 18, color: C.navy }}>Live line state — grounded in Unity Catalog</span>
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  {STATE_CHIPS.map((c, i) => <StateChip key={i} chip={c} lt={lt} />)}
                </div>
              </div>
            </Reveal>
          )}

          {/* specialists */}
          {showSpec && (
            <Reveal lt={lt} at={6.0} dur={0.4} from="up">
              <div>
                <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 17, color: C.inkSoft, marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Glyph kind="spark" size={18} color={C.coral} /> Routing to specialist agents…
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  {SPECIALISTS.slice(1).map((s, i) => <SpecialistChip key={i} s={s} lt={lt} />)}
                </div>
              </div>
            </Reveal>
          )}

          {/* running scenarios */}
          {showRun && (
            <Reveal lt={lt} at={10.4} dur={0.4} from="up">
              <div style={{ background: C.navy, borderRadius: 14, padding: 20, color: C.white }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 19 }}>Stress-testing the schedule</span>
                  <span style={{ fontFamily: MONO, fontSize: 16, color: C.coral }}>{Math.round(runP * 1000)} / 1,000 scenarios</span>
                </div>
                <div style={{ height: 10, borderRadius: 5, background: 'rgba(255,255,255,.12)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${runP * 100}%`, background: `linear-gradient(90deg, ${C.coral}, ${C.lava})`, borderRadius: 5 }}/>
                </div>
                <div style={{ display: 'flex', gap: 18, marginTop: 14, fontFamily: MONO, fontSize: 15, color: 'rgba(255,255,255,.65)' }}>
                  <span>Monte-Carlo forecast</span><span>·</span><span>MILP solve (OR-Tools)</span><span>·</span><span>AI Search: similar cases</span>
                </div>
              </div>
            </Reveal>
          )}

          {/* plan card */}
          {showPlan && (
            <Reveal lt={lt} at={20.0} dur={0.5} from="up">
              <div style={{ background: C.white, border: `1px solid ${C.line}`, borderRadius: 16, padding: 24, boxShadow: '0 10px 30px rgba(27,49,57,.10)' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 23, color: C.navy }}>Recommended recovery plan</span>
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 15, color: C.green, background: 'rgba(0,169,114,.10)', borderRadius: 999, padding: '6px 14px' }}>provably optimal · MILP</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {ACTIONS.map((a, i) => (
                    <Reveal key={i} lt={lt} at={a.at} dur={0.4} from="left" dist={14}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 14, background: C.oat, borderRadius: 10, padding: '13px 16px' }}>
                        <span style={{ width: 26, height: 26, borderRadius: 999, background: C.navy, color: C.white, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO, fontSize: 15, fontWeight: 500 }}>{i + 1}</span>
                        <div style={{ display: 'flex', flexDirection: 'column' }}>
                          <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 20, color: C.ink }}>{a.t}</span>
                          <span style={{ fontFamily: SANS, fontSize: 15, color: C.inkMuted }}>{a.d}</span>
                        </div>
                      </div>
                    </Reveal>
                  ))}
                </div>
                {/* trade-off + confidence */}
                <Reveal lt={lt} at={25.0} dur={0.5} from="up">
                  <div style={{ display: 'flex', gap: 14, marginTop: 16 }}>
                    <div style={{ flex: 1, background: C.oatMed, borderRadius: 12, padding: 16 }}>
                      <div style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, color: C.inkMuted, marginBottom: 8, letterSpacing: '0.02em' }}>TRADE-OFF, MADE EXPLICIT</div>
                      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                        <span style={{ fontFamily: MONO, fontSize: 26, fontWeight: 500, color: C.navy }}>€420</span>
                        <span style={{ fontFamily: SANS, fontSize: 15, color: C.inkSoft }}>overtime</span>
                        <span style={{ fontFamily: SANS, fontSize: 18, color: C.inkMuted, margin: '0 4px' }}>vs</span>
                        <span style={{ fontFamily: MONO, fontSize: 26, fontWeight: 500, color: C.lava }}>€11,200</span>
                        <span style={{ fontFamily: SANS, fontSize: 15, color: C.inkSoft }}>missed shipment avoided</span>
                      </div>
                    </div>
                    <div style={{ width: 220, background: C.navy, borderRadius: 12, padding: 16, color: C.white }}>
                      <div style={{ fontFamily: SANS, fontSize: 14, fontWeight: 600, color: 'rgba(255,255,255,.6)', marginBottom: 6, letterSpacing: '0.02em' }}>CONFIDENCE</div>
                      <div style={{ fontFamily: MONO, fontSize: 38, fontWeight: 500, color: C.green, lineHeight: 1 }}>92%</div>
                      <div style={{ fontFamily: SANS, fontSize: 14, color: 'rgba(255,255,255,.7)', marginTop: 4 }}>to hit target · P50 8,300 cases</div>
                    </div>
                  </div>
                </Reveal>
                <Reveal lt={lt} at={29.0} dur={0.4} from="up">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 14, fontFamily: SANS, fontSize: 15, color: C.inkSoft }}>
                    <Glyph kind="check" size={18} color={C.slate} /> 3 similar past stoppages found · avg recovery 41 min
                  </div>
                </Reveal>
                {/* approve */}
                <Reveal lt={lt} at={31.0} dur={0.5} from="up">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 22, paddingTop: 20, borderTop: `1px solid ${C.line}` }}>
                    <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12, background: approved ? C.green : C.lava, borderRadius: 12, padding: '15px 26px', boxShadow: tap ? `0 0 0 6px rgba(255,54,33,.18)` : '0 6px 16px rgba(255,54,33,.25)', transform: `scale(${tap && !approved ? 0.97 : 1})`, transition: 'none' }}>
                      <Glyph kind="check" size={22} color={C.white} />
                      <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 20, color: C.white }}>{approved ? 'Approved' : 'Approve plan'}</span>
                    </div>
                    <div style={{ background: C.white, border: `1px solid ${C.line}`, borderRadius: 12, padding: '15px 24px', fontFamily: SANS, fontWeight: 600, fontSize: 20, color: C.navy }}>Adjust</div>
                    {approved && (
                      <Reveal lt={lt} at={35.7} dur={0.4} from="left" style={{ marginLeft: 4 }}>
                        <span style={{ fontFamily: SANS, fontSize: 16, color: C.green, fontWeight: 600 }}>ROADMAP · MCP → CMMS · QMS · MES</span>
                      </Reveal>
                    )}
                  </div>
                </Reveal>
              </div>
            </Reveal>
          )}
          <div style={{ height: 80 }} />
        </div>

        {/* cursor that taps Approve */}
        {lt > 33.4 && lt < 37 && (() => {
          const p = clamp((lt - 33.4) / 1.5, 0, 1);
          const cx = interpolate([0, 1], [980, 372], Easing.easeInOutCubic)(p);
          const cy = interpolate([0, 1], [120, 612], Easing.easeInOutCubic)(p);
          return <div style={{ position: 'absolute', left: cx, top: cy, zIndex: 5 }}><Glyph kind="cursor" size={40} color={C.navy} /></div>;
        })()}
      </div>
    </div>
  );
}

// ── Surface-style tablet ─────────────────────────────────────────────────────────
function SurfaceTablet({ w, children }) {
  const h = Math.round(w * 0.668);
  return (
    <div style={{ width: w, height: h, position: 'relative' }}>
      {/* kickstand */}
      <div style={{ position: 'absolute', left: w * 0.18, right: w * 0.18, bottom: -18, height: 34, background: '#1d2022', borderRadius: '0 0 14px 14px', transform: 'perspective(600px) rotateX(46deg)', transformOrigin: 'top', opacity: 0.6 }}/>
      {/* body */}
      <div style={{ position: 'absolute', inset: 0, background: 'linear-gradient(150deg,#3a3d40,#26292b)', borderRadius: 22, padding: 16, boxShadow: '0 50px 110px rgba(11,32,38,.5), 0 8px 24px rgba(11,32,38,.4)' }}>
        <div style={{ width: '100%', height: '100%', borderRadius: 10, overflow: 'hidden', position: 'relative', background: C.oat }}>
          {children}
        </div>
        {/* front camera */}
        <div style={{ position: 'absolute', top: 24, left: '50%', width: 7, height: 7, borderRadius: 4, marginLeft: -3, background: '#0c0d0e' }}/>
      </div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
//  SCENES
// ═════════════════════════════════════════════════════════════════════════════

// 1 — establishing: the line, running
function Scene1() {
  return (
    <Scene start={0} end={9.2}>
      {(lt) => {
        const flow = lt * 118;
        const zs = interpolate([0, 9], [1.06, 1.14], Easing.easeInOutSine)(lt);
        const zx = interpolate([0, 9], [40, -60], Easing.linear)(lt);
        return (
          <div style={{ position: 'absolute', inset: 0 }}>
            <Zoom scale={zs} x={zx} y={10}>
              <BreweryLine flow={flow} t={lt} />
            </Zoom>
            {/* lower third */}
            <div style={{ position: 'absolute', left: 96, bottom: 90 }}>
              <Reveal lt={lt} at={0.6} dur={0.6}><Eyebrow>A day on the line</Eyebrow></Reveal>
              <Reveal lt={lt} at={1.0} dur={0.6} style={{ marginTop: 12 }}>
                <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 64, color: C.navy, letterSpacing: '-0.02em', lineHeight: 1.02 }}>{CFG.name} runs Packaging Line 4</div>
              </Reveal>
              <Reveal lt={lt} at={1.5} dur={0.6} style={{ marginTop: 14 }}>
                <div style={{ fontFamily: SANS, fontSize: 27, color: C.inkSoft }}>19 connected machines · live OEE, throughput, quality and material status.</div>
              </Reveal>
            </div>
            <div style={{ position: 'absolute', right: 96, top: 70 }}>
              {CFG.showClock && <Reveal lt={lt} at={1.4} dur={0.5}><Clock time="06:52" /></Reveal>}
            </div>
          </div>
        );
      }}
    </Scene>
  );
}

// 2 — the old way: cognitive overload
function Scene2() {
  return (
    <Scene start={9.0} end={22.2}>
      {(lt) => {
        const cx = 700, cy = 470;
        const icons = [
          { kind: 'sheet', label: 'Shift report.xlsx', a: -1.4, rx: 360, ry: 200, sp: 0.42 },
          { kind: 'radio', label: '“Filler okay?”', a: 0.4, rx: 420, ry: 170, sp: -0.5 },
          { kind: 'clipboard', label: 'SOP checklist', a: 1.9, rx: 330, ry: 230, sp: 0.55 },
          { kind: 'bell', label: '3 new alarms', a: 3.0, rx: 400, ry: 150, sp: -0.4 },
          { kind: 'chart', label: 'OEE dashboard', a: 4.3, rx: 350, ry: 215, sp: 0.48 },
          { kind: 'phone', label: 'Maintenance call', a: 5.4, rx: 430, ry: 185, sp: -0.46 },
        ];
        return (
          <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(180deg,#EDEAE3,#F4F1EA)` }}>
            <Maya x={cx - 75} y={cy - 30} scale={1.5} />
            {icons.map((ic, i) => (
              <ChaosIcon key={i} kind={ic.kind} label={ic.label} cx={cx} cy={cy - 60} baseAngle={ic.a} rx={ic.rx} ry={ic.ry} lt={lt} speed={ic.sp} />
            ))}
            <div style={{ position: 'absolute', right: 110, top: 150, maxWidth: 620, textAlign: 'right' }}>
              <Reveal lt={lt} at={0.5} dur={0.6}><Eyebrow>The old way</Eyebrow></Reveal>
              <Reveal lt={lt} at={0.9} dur={0.6} style={{ marginTop: 12 }}>
                <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 60, color: C.navy, letterSpacing: '-0.02em', lineHeight: 1.04 }}>Data-rich.<br/>Insight-poor.</div>
              </Reveal>
              <Reveal lt={lt} at={1.6} dur={0.6} style={{ marginTop: 16 }}>
                <div style={{ fontFamily: SANS, fontSize: 26, color: C.inkSoft, lineHeight: 1.4 }}>Spreadsheets, radio calls and dashboards — and the root-cause meeting happens tomorrow.</div>
              </Reveal>
            </div>
            <div style={{ position: 'absolute', left: 110, bottom: 80 }}>
              {CFG.showClock && <Reveal lt={lt} at={3.0} dur={0.6}><Clock time="09:18" /></Reveal>}
            </div>
          </div>
        );
      }}
    </Scene>
  );
}


// 3 — the control tower: one live view across OT and IT sources
function SceneControlTower() {
  return (
    <Scene start={22.0} end={36.2}>
      {(lt) => {
        const sources = [
          { name: 'SCADA', detail: 'machine state', color: C.navy700 },
          { name: 'MES', detail: 'plan + output', color: C.lava },
          { name: 'SAP MM', detail: 'materials', color: C.amber },
          { name: 'LIMS', detail: 'quality', color: C.green },
          { name: 'CMMS', detail: 'maintenance', color: C.slate },
        ];
        const metrics = [
          { label: 'OEE', value: '82.4%', note: 'on target', color: C.green },
          { label: 'THROUGHPUT', value: '5,940', note: 'bottles / hr', color: C.navy700 },
          { label: 'LINE STATUS', value: '19 / 19', note: 'machines running', color: C.green },
          { label: 'ACTIVE EVENTS', value: '0', note: 'line stable', color: C.navy700 },
        ];
        const flow = lt * 116;
        return (
          <div style={{ position: 'absolute', inset: 0, background: `linear-gradient(180deg, ${C.oat}, ${C.oatMed})` }}>
            <div style={{ position: 'absolute', inset: '0 0 430px 0', opacity: 0.24 }}>
              <Zoom scale={0.9} x={0} y={-90}>
                <BreweryLine flow={flow} t={lt} statuses={['run','run','run','run','run']} />
              </Zoom>
            </div>

            <div style={{ position: 'absolute', left: 96, right: 96, top: 58 }}>
              <Reveal lt={lt} at={0.3} dur={0.5}><Eyebrow>THE CONTROL TOWER</Eyebrow></Reveal>
              <Reveal lt={lt} at={0.7} dur={0.6} style={{ marginTop: 10 }}>
                <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 58, color: C.navy, letterSpacing: '-0.025em' }}>
                  One live view of the whole line.
                </div>
              </Reveal>
              <Reveal lt={lt} at={1.2} dur={0.6} style={{ marginTop: 10 }}>
                <div style={{ fontFamily: SANS, fontSize: 25, color: C.inkSoft }}>
                  Machine signals, production context and business data — together while the shift is running.
                </div>
              </Reveal>
            </div>

            <div style={{ position: 'absolute', left: 96, right: 96, top: 270, display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 18 }}>
              {metrics.map((m, i) => (
                <Reveal key={m.label} lt={lt} at={2.0 + i * 0.18} dur={0.5} from="up" dist={16}>
                  <div style={{ background: C.white, border: `1px solid ${C.line}`, borderTop: `5px solid ${m.color}`, borderRadius: 14, padding: '24px 26px', boxShadow: '0 14px 32px rgba(27,49,57,.08)' }}>
                    <div style={{ fontFamily: MONO, fontSize: 14, color: C.inkMuted, letterSpacing: '.08em' }}>{m.label}</div>
                    <div style={{ fontFamily: MONO, fontSize: 42, fontWeight: 500, color: C.navy, marginTop: 10 }}>{m.value}</div>
                    <div style={{ fontFamily: SANS, fontSize: 17, color: m.color, marginTop: 5 }}>{m.note}</div>
                  </div>
                </Reveal>
              ))}
            </div>

            <Reveal lt={lt} at={4.0} dur={0.5} style={{ position: 'absolute', left: 96, right: 96, top: 560 }}>
              <div style={{ background: C.navy, borderRadius: 16, padding: '26px 30px 30px', boxShadow: '0 22px 50px rgba(11,32,38,.18)' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <div>
                    <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 24, color: C.white }}>Live operational context</div>
                    <div style={{ fontFamily: SANS, fontSize: 17, color: 'rgba(255,255,255,.62)', marginTop: 4 }}>Different systems, one shared picture of the shift</div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 9, color: C.green, fontFamily: MONO, fontSize: 15 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 9, background: C.green, boxShadow: '0 0 0 5px rgba(0,169,114,.16)' }} />
                    LIVE
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12, marginTop: 22 }}>
                  {sources.map((s, i) => (
                    <Reveal key={s.name} lt={lt} at={4.6 + i * 0.22} dur={0.45} from="up" dist={12}>
                      <div style={{ border: '1px solid rgba(255,255,255,.16)', background: 'rgba(255,255,255,.07)', borderRadius: 10, padding: '15px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
                        <span style={{ width: 10, height: 10, borderRadius: 10, background: s.color, flex: '0 0 auto' }} />
                        <div>
                          <div style={{ fontFamily: MONO, fontSize: 15, fontWeight: 500, color: C.white }}>{s.name}</div>
                          <div style={{ fontFamily: SANS, fontSize: 14, color: 'rgba(255,255,255,.56)', marginTop: 2 }}>{s.detail}</div>
                        </div>
                      </div>
                    </Reveal>
                  ))}
                </div>
              </div>
            </Reveal>
          </div>
        );
      }}
    </Scene>
  );
}

// 3 — the incident
function Scene3() {
  return (
    <Scene start={36.0} end={50.2}>
      {(lt) => {
        // line runs ~1.4s then stops hard
        const flow = interpolate([0, 1.4, 2.0], [0, 150, 168], Easing.easeOutCubic)(lt);
        const statuses = lt < 1.5 ? ['run','run','run','run','run'] : ['run','stop','idle','idle','idle'];
        const zs = interpolate([0, 1.5, 9], [1.0, 1.0, 1.22], [Easing.linear, Easing.easeInOutCubic])(lt);
        const zx = interpolate([1.5, 9], [0, -180], Easing.easeInOutCubic)(lt);
        const zy = interpolate([1.5, 9], [0, 40], Easing.easeInOutCubic)(lt);
        const shake = lt > 1.5 && lt < 2.0 ? Math.sin(lt * 80) * 5 : 0;
        return (
          <div style={{ position: 'absolute', inset: 0 }}>
            <Zoom scale={zs} x={zx + shake} y={zy}>
              <BreweryLine flow={flow} t={lt} statuses={statuses} alert={lt > 1.5} />
            </Zoom>
            {/* alert banner */}
            {lt > 1.7 && (
              <Reveal lt={lt} at={1.7} dur={0.4} from="down" dist={24}>
                <div style={{ position: 'absolute', left: '50%', top: 64, transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 14, background: C.lava, color: C.white, borderRadius: 12, padding: '16px 28px', boxShadow: '0 12px 30px rgba(255,54,33,.35)' }}>
                  <Glyph kind="bell" size={26} color={C.white} />
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 30, letterSpacing: '-0.01em' }}>Filler #3 down — line cascading</span>
                </div>
              </Reveal>
            )}
            {/* stat readout */}
            {lt > 3.0 && (
              <div style={{ position: 'absolute', left: 110, bottom: 96, display: 'flex', gap: 28 }}>
                {[['6,000','bottles/hr stopped'],['71%','line OEE, falling'],['€300K','per OEE point / year']].map((s, i) => (
                  <Reveal key={i} lt={lt} at={3.0 + i * 0.25} dur={0.5} from="up">
                    <div>
                      <div style={{ fontFamily: MONO, fontSize: 52, fontWeight: 500, color: C.lava, lineHeight: 1 }}>{s[0]}</div>
                      <div style={{ fontFamily: SANS, fontSize: 20, color: C.navy, marginTop: 6 }}>{s[1]}</div>
                    </div>
                  </Reveal>
                ))}
              </div>
            )}
            {lt > 5.4 && (
              <Reveal lt={lt} at={5.4} dur={0.6} style={{ position: 'absolute', right: 110, bottom: 110, maxWidth: 540, textAlign: 'right' }}>
                <div style={{ fontFamily: SANS, fontSize: 27, color: C.navy, fontWeight: 500, lineHeight: 1.35 }}>Recovery decisions — speed, overtime, CIP — used to ride on gut feel.</div>
              </Reveal>
            )}
            <div style={{ position: 'absolute', right: 96, top: 70 }}>
              {CFG.showClock && <Reveal lt={lt} at={0.2} dur={0.4}><Clock time="11:47" color={C.lava} /></Reveal>}
            </div>
          </div>
        );
      }}
    </Scene>
  );
}

// 4–6 — the copilot (shared tablet, building UI)
function CopilotAct() {
  return (
    <Sprite start={50} end={94.5}>
      {({ localTime: lt }) => {
        // tablet entrance: from Maya's hands (small, lower-left) to hero center
        const s = interpolate([0, 3.2], [0.6, 1], Easing.easeOutCubic)(lt);
        const tx = interpolate([0, 3.2], [-360, 0], Easing.easeOutCubic)(lt);
        const ty = interpolate([0, 3.2], [230, 0], Easing.easeOutCubic)(lt);
        const tabletW = 1240;
        const tabletH = Math.round(tabletW * 0.668);
        const fade = Math.min(clamp(lt / 0.6, 0, 1), clamp((44.5 - lt) / 0.6, 0, 1));
        const mayaFade = clamp((6.5 - lt) / 1.2, 0, 1);
        const captions = [
          { a: 0.4, b: 11.5, t: 'Ask the question you need answered.' },
          { a: 11.5, b: 19.5, t: 'Eight specialist agents reason over the live line.' },
          { a: 19.5, b: 28.5, t: 'Options, assumptions and trade-offs — in one place.' },
          { a: 28.5, b: 44, t: 'Maya reviews the plan before anything changes.' },
        ];
        return (
          <div style={{ position: 'absolute', inset: 0, opacity: fade }}>
            {/* dim line backdrop */}
            <BreweryLine flow={168} t={lt} statuses={['run','stop','idle','idle','idle']} dim={true} />
            {/* Maya holding the tablet up, lower-left */}
            {mayaFade > 0 && <div style={{ opacity: mayaFade }}><Maya x={150} y={640} scale={1.7} /></div>}
            {/* caption top-left */}
            <div style={{ position: 'absolute', left: 96, top: 60, maxWidth: 470 }}>
              <Eyebrow color={C.coral}>ProdLine Copilot</Eyebrow>
              {captions.map((c, i) => {
                const on = lt >= c.a && lt < c.b;
                const o = on ? Math.min(clamp((lt - c.a) / 0.4, 0, 1), clamp((c.b - lt) / 0.4, 0, 1)) : 0;
                if (o <= 0) return null;
                return <div key={i} style={{ position: 'absolute', top: 30, opacity: o, fontFamily: SANS, fontWeight: 700, fontSize: 38, color: C.white, letterSpacing: '-0.01em', lineHeight: 1.15, transform: `translateY(${(1 - o) * 10}px)` }}>{c.t}</div>;
              })}
            </div>
            {/* tablet */}
            <div style={{ position: 'absolute', left: 1920 - tabletW - 60, top: 150, transform: `translate(${tx}px,${ty}px) scale(${s})`, transformOrigin: 'center', width: tabletW }}>
              <SurfaceTablet w={tabletW}>
                <CopilotUI lt={lt} />
              </SurfaceTablet>
            </div>
          </div>
        );
      }}
    </Sprite>
  );
}

// 7 — resolution
function Scene7() {
  return (
    <Scene start={94.3} end={102.2}>
      {(lt) => {
        const flow = interpolate([0, 4], [0, 520], Easing.easeInCubic)(lt);
        const zs = interpolate([0, 7], [1.16, 1.04], Easing.easeOutSine)(lt);
        return (
          <div style={{ position: 'absolute', inset: 0 }}>
            <Zoom scale={zs} x={-40} y={20}>
              <BreweryLine flow={flow} t={lt} statuses={['run','run','run','run','run']} />
            </Zoom>
            <Maya x={1560} y={690} scale={1.4} calm={true} />
            {lt > 0.6 && (
              <Reveal lt={lt} at={0.6} dur={0.4} from="down" dist={20}>
                <div style={{ position: 'absolute', left: '50%', top: 70, transform: 'translateX(-50%)', display: 'flex', alignItems: 'center', gap: 12, background: C.green, color: C.white, borderRadius: 12, padding: '14px 26px', boxShadow: '0 12px 28px rgba(0,169,114,.3)' }}>
                  <Glyph kind="check" size={24} color={C.white} />
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 28 }}>Filler #3 back online · recovery plan running</span>
                </div>
              </Reveal>
            )}
            <div style={{ position: 'absolute', left: 110, bottom: 92, maxWidth: 900 }}>
              <Reveal lt={lt} at={1.6} dur={0.6}>
                <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 52, color: C.navy, letterSpacing: '-0.02em', lineHeight: 1.05 }}>Back on target in minutes — not tomorrow.</div>
              </Reveal>
            </div>
            <div style={{ position: 'absolute', right: 96, top: 70 }}>
              {CFG.showClock && <Reveal lt={lt} at={0.3} dur={0.4}><Clock time="11:53" color={C.green} /></Reveal>}
            </div>
          </div>
        );
      }}
    </Scene>
  );
}

// 8 — close / brand
function Scene8() {
  return (
    <Scene start={102.0} end={110}>
      {(lt) => {
        const points = ['A live view of the line', 'Recommendations grounded in context', 'You keep control', 'All sources, one governed view'];
        return (
          <div style={{ position: 'absolute', inset: 0, background: `radial-gradient(1200px 800px at 30% 20%, #12313b, ${C.inkDeep})` }}>
            {/* subtle node motif */}
            <div style={{ position: 'absolute', right: 90, top: 90, opacity: 0.5 }}>
              <Reveal lt={lt} at={0.3} dur={0.8}><img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iNzEzIiBoZWlnaHQ9IjExMyIgdmlld0JveD0iMCAwIDcxMyAxMTMiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxnIGNsaXAtcGF0aD0idXJsKCNjbGlwMF8xOTg0XzczMSkiPgo8cGF0aCBkPSJNOTguOSA0Ni41OTk2TDUyLjMgNzIuODk5NkwyLjQgNDQuNzk5NkwwIDQ2LjA5OTZWNjYuNDk5Nkw1Mi4zIDk1Ljg5OTZMOTguOSA2OS42OTk2VjgwLjQ5OTZMNTIuMyAxMDYuOEwyLjQgNzguNjk5NkwwIDc5Ljk5OTZWODMuNDk5Nkw1Mi4zIDExMi45TDEwNC41IDgzLjQ5OTZWNjMuMDk5NkwxMDIuMSA2MS43OTk2TDUyLjMgODkuNzk5Nkw1LjYgNjMuNTk5NlY1Mi43OTk2TDUyLjMgNzguOTk5NkwxMDQuNSA0OS41OTk2VjI5LjQ5OTZMMTAxLjkgMjcuOTk5Nkw1Mi4zIDU1Ljg5OTZMOCAzMS4wOTk2TDUyLjMgNi4xOTk2MUw4OC43IDI2LjY5OTZMOTEuOSAyNC44OTk2VjIyLjM5OTZMNTIuMyAwLjA5OTYwOTRMMCAyOS40OTk2VjMyLjY5OTZMNTIuMyA2Mi4wOTk2TDk4LjkgMzUuNzk5NlY0Ni41OTk2WiIgZmlsbD0iI0ZGMzYyMSI+PC9wYXRoPgo8cGF0aCBkPSJNMjAyLjIgOTYuMTk5NFY2LjM5OTQxSDE4OC40VjM5Ljk5OTRDMTg4LjQgNDAuNDk5NCAxODguMSA0MC44OTk0IDE4Ny42IDQxLjA5OTRDMTg3LjEgNDEuMjk5NCAxODYuNiA0MS4wOTk0IDE4Ni4zIDQwLjc5OTRDMTgxLjYgMzUuMjk5NCAxNzQuMyAzMi4xOTk0IDE2Ni4zIDMyLjE5OTRDMTQ5LjIgMzIuMTk5NCAxMzUuOCA0Ni41OTk0IDEzNS44IDY0Ljk5OTRDMTM1LjggNzMuOTk5NCAxMzguOSA4Mi4yOTk0IDE0NC42IDg4LjM5OTRDMTUwLjMgOTQuNDk5NCAxNTggOTcuNzk5NCAxNjYuMyA5Ny43OTk0QzE3NC4yIDk3Ljc5OTQgMTgxLjUgOTQuNDk5NCAxODYuMyA4OC43OTk0QzE4Ni42IDg4LjM5OTQgMTg3LjIgODguMjk5NCAxODcuNiA4OC4zOTk0QzE4OC4xIDg4LjU5OTQgMTg4LjQgODguOTk5NCAxODguNCA4OS40OTk0Vjk2LjE5OTRIMjAyLjJaTTE2OS4zIDg1LjI5OTRDMTU4LjMgODUuMjk5NCAxNDkuNyA3Ni4zOTk0IDE0OS43IDY0Ljk5OTRDMTQ5LjcgNTMuNTk5NCAxNTguMyA0NC42OTk0IDE2OS4zIDQ0LjY5OTRDMTgwLjMgNDQuNjk5NCAxODguOSA1My41OTk0IDE4OC45IDY0Ljk5OTRDMTg4LjkgNzYuMzk5NCAxODAuMyA4NS4yOTk0IDE2OS4zIDg1LjI5OTRaIiBmaWxsPSJ3aGl0ZSI+PC9wYXRoPgo8cGF0aCBkPSJNMjc2IDk2LjA5OTZWMzMuNjk5NkgyNjIuM1YzOS45OTk2QzI2Mi4zIDQwLjQ5OTYgMjYyIDQwLjg5OTYgMjYxLjUgNDEuMDk5NkMyNjEgNDEuMjk5NiAyNjAuNSA0MS4wOTk2IDI2MC4yIDQwLjY5OTZDMjU1LjYgMzUuMTk5NiAyNDguNCAzMi4wOTk2IDI0MC4yIDMyLjA5OTZDMjIzLjEgMzIuMDk5NiAyMDkuNyA0Ni40OTk2IDIwOS43IDY0Ljg5OTZDMjA5LjcgODMuMjk5NiAyMjMuMSA5Ny42OTk2IDI0MC4yIDk3LjY5OTZDMjQ4LjEgOTcuNjk5NiAyNTUuNCA5NC4zOTk2IDI2MC4yIDg4LjU5OTZDMjYwLjUgODguMTk5NiAyNjEuMSA4OC4wOTk2IDI2MS41IDg4LjE5OTZDMjYyIDg4LjM5OTYgMjYyLjMgODguNzk5NiAyNjIuMyA4OS4yOTk2Vjk2LjA5OTZIMjc2Wk0yNDMuMyA4NS4yOTk2QzIzMi4zIDg1LjI5OTYgMjIzLjcgNzYuMzk5NiAyMjMuNyA2NC45OTk2QzIyMy43IDUzLjU5OTYgMjMyLjMgNDQuNjk5NiAyNDMuMyA0NC42OTk2QzI1NC4zIDQ0LjY5OTYgMjYyLjkgNTMuNTk5NiAyNjIuOSA2NC45OTk2QzI2Mi45IDc2LjM5OTYgMjU0LjMgODUuMjk5NiAyNDMuMyA4NS4yOTk2WiIgZmlsbD0id2hpdGUiPjwvcGF0aD4KPHBhdGggZD0iTTM5My4zIDk2LjE5OTZWMzMuNjk5NkgzNzkuNlYzOS45OTk2QzM3OS42IDQwLjQ5OTYgMzc5LjMgNDAuODk5NiAzNzguOCA0MS4wOTk2QzM3OC4zIDQxLjI5OTYgMzc3LjggNDEuMDk5NiAzNzcuNSA0MC42OTk2QzM3Mi45IDM1LjE5OTYgMzY1LjcgMzIuMDk5NiAzNTcuNSAzMi4wOTk2QzM0MC4zIDMyLjA5OTYgMzI3IDQ2LjQ5OTYgMzI3IDY0Ljk5OTZDMzI3IDgzLjQ5OTYgMzQwLjQgOTcuNzk5NiAzNTcuNSA5Ny43OTk2QzM2NS40IDk3Ljc5OTYgMzcyLjcgOTQuNDk5NiAzNzcuNSA4OC42OTk2QzM3Ny44IDg4LjI5OTYgMzc4LjQgODguMTk5NiAzNzguOCA4OC4yOTk2QzM3OS4zIDg4LjQ5OTYgMzc5LjYgODguODk5NiAzNzkuNiA4OS4zOTk2Vjk2LjE5OTZIMzkzLjNaTTM2MC42IDg1LjI5OTZDMzQ5LjYgODUuMjk5NiAzNDEgNzYuMzk5NiAzNDEgNjQuOTk5NkMzNDEgNTMuNTk5NiAzNDkuNiA0NC42OTk2IDM2MC42IDQ0LjY5OTZDMzcxLjYgNDQuNjk5NiAzODAuMiA1My41OTk2IDM4MC4yIDY0Ljk5OTZDMzgwLjIgNzYuMzk5NiAzNzEuNiA4NS4yOTk2IDM2MC42IDg1LjI5OTZaIiBmaWxsPSJ3aGl0ZSI+PC9wYXRoPgo8cGF0aCBkPSJNNDE4LjQgODguNzk5NEM0MTguNSA4OC43OTk0IDQxOC43IDg4LjY5OTQgNDE4LjggODguNjk5NEM0MTkuMSA4OC42OTk0IDQxOS41IDg4Ljg5OTQgNDE5LjcgODkuMDk5NEM0MjQuNCA5NC41OTk0IDQzMS43IDk3LjY5OTQgNDM5LjcgOTcuNjk5NEM0NTYuOCA5Ny42OTk0IDQ3MC4yIDgzLjI5OTQgNDcwLjIgNjQuODk5NEM0NzAuMiA1NS44OTk0IDQ2Ny4xIDQ3LjU5OTQgNDYxLjQgNDEuNDk5NEM0NTUuNyAzNS4zOTk0IDQ0OCAzMi4wOTk0IDQzOS43IDMyLjA5OTRDNDMxLjggMzIuMDk5NCA0MjQuNSAzNS4zOTk0IDQxOS43IDQxLjA5OTRDNDE5LjQgNDEuNDk5NCA0MTguOSA0MS41OTk0IDQxOC40IDQxLjQ5OTRDNDE3LjkgNDEuMjk5NCA0MTcuNiA0MC44OTk0IDQxNy42IDQwLjM5OTRWNi4zOTk0MUg0MDMuOFY5Ni4xOTk0SDQxNy42Vjg5Ljg5OTRDNDE3LjYgODkuMzk5NCA0MTcuOSA4OC45OTk0IDQxOC40IDg4Ljc5OTRaTTQxNyA2NC45OTk0QzQxNyA1My41OTk0IDQyNS42IDQ0LjY5OTQgNDM2LjYgNDQuNjk5NEM0NDcuNiA0NC42OTk0IDQ1Ni4yIDUzLjU5OTQgNDU2LjIgNjQuOTk5NEM0NTYuMiA3Ni4zOTk0IDQ0Ny42IDg1LjI5OTQgNDM2LjYgODUuMjk5NEM0MjUuNiA4NS4yOTk0IDQxNyA3Ni4yOTk0IDQxNyA2NC45OTk0WiIgZmlsbD0id2hpdGUiPjwvcGF0aD4KPHBhdGggZD0iTTUxMC4xIDQ2LjQ5OTRDNTExLjQgNDYuNDk5NCA1MTIuNiA0Ni41OTk0IDUxMy40IDQ2Ljc5OTRWMzIuNTk5NEM1MTIuOSAzMi40OTk0IDUxMiAzMi4zOTk0IDUxMS4xIDMyLjM5OTRDNTAzLjkgMzIuMzk5NCA0OTcuMyAzNi4wOTk0IDQ5My44IDQxLjk5OTRDNDkzLjUgNDIuNDk5NCA0OTMgNDIuNjk5NCA0OTIuNSA0Mi40OTk0QzQ5MiA0Mi4zOTk0IDQ5MS42IDQxLjg5OTQgNDkxLjYgNDEuMzk5NFYzMy42OTk0SDQ3Ny45Vjk2LjI5OTRINDkxLjdWNjguNjk5NEM0OTEuNyA1NC45OTk0IDQ5OC43IDQ2LjQ5OTQgNTEwLjEgNDYuNDk5NFoiIGZpbGw9IndoaXRlIj48L3BhdGg+CjxwYXRoIGQ9Ik01MzYuMSAzMy42OTkySDUyMi4xVjk2LjI5OTJINTM2LjFWMzMuNjk5MloiIGZpbGw9IndoaXRlIj48L3BhdGg+CjxwYXRoIGQ9Ik01MjguOSA2LjQ5OTAyQzUyNC4yIDYuNDk5MDIgNTIwLjQgMTAuMjk5IDUyMC40IDE0Ljk5OUM1MjAuNCAxOS42OTkgNTI0LjIgMjMuNDk5IDUyOC45IDIzLjQ5OUM1MzMuNiAyMy40OTkgNTM3LjQgMTkuNjk5IDUzNy40IDE0Ljk5OUM1MzcuNCAxMC4yOTkgNTMzLjYgNi40OTkwMiA1MjguOSA2LjQ5OTAyWiIgZmlsbD0id2hpdGUiPjwvcGF0aD4KPHBhdGggZD0iTTU3Ny4xIDMyLjA5OTZDNTU3LjkgMzIuMDk5NiA1NDQgNDUuODk5NiA1NDQgNjQuOTk5NkM1NDQgNzQuMjk5NiA1NDcuMyA4Mi41OTk2IDU1My4yIDg4LjU5OTZDNTU5LjIgOTQuNTk5NiA1NjcuNiA5Ny44OTk2IDU3NyA5Ny44OTk2QzU4NC44IDk3Ljg5OTYgNTkwLjggOTYuMzk5NiA2MDIuMiA4Ny45OTk2TDU5NC4zIDc5LjY5OTZDNTg4LjcgODMuMzk5NiA1ODMuNSA4NS4xOTk2IDU3OC40IDg1LjE5OTZDNTY2LjggODUuMTk5NiA1NTguMSA3Ni40OTk2IDU1OC4xIDY0Ljk5OTZDNTU4LjEgNTMuNDk5NiA1NjYuOCA0NC43OTk2IDU3OC40IDQ0Ljc5OTZDNTgzLjkgNDQuNzk5NiA1ODkgNDYuNTk5NiA1OTQuMSA1MC4yOTk2TDYwMi45IDQxLjk5OTZDNTkyLjYgMzMuMTk5NiA1ODMuMyAzMi4wOTk2IDU3Ny4xIDMyLjA5OTZaIiBmaWxsPSJ3aGl0ZSI+PC9wYXRoPgo8cGF0aCBkPSJNNjI2LjUgNjguNjk5QzYyNi43IDY4LjQ5OSA2MjcgNjguMzk5IDYyNy4zIDY4LjM5OUg2MjcuNEM2MjcuNyA2OC4zOTkgNjI4IDY4LjU5OSA2MjguMyA2OC43OTlMNjUwLjQgOTYuMTk5SDY2Ny40TDYzOC44IDYxLjU5OUM2MzguNCA2MS4wOTkgNjM4LjQgNjAuMzk5IDYzOC45IDU5Ljk5OUw2NjUuMiAzMy42OTlINjQ4LjNMNjI1LjYgNTYuNDk5QzYyNS4zIDU2Ljc5OSA2MjQuOCA1Ni44OTkgNjI0LjMgNTYuNzk5QzYyMy45IDU2LjU5OSA2MjMuNiA1Ni4xOTkgNjIzLjYgNTUuNjk5VjYuNDk5MDJINjA5LjdWOTYuMjk5SDYyMy41VjcxLjg5OUM2MjMuNSA3MS41OTkgNjIzLjYgNzEuMTk5IDYyMy45IDcwLjk5OUw2MjYuNSA2OC42OTlaIiBmaWxsPSJ3aGl0ZSI+PC9wYXRoPgo8cGF0aCBkPSJNNjg5LjkgOTcuNzk5QzcwMS4yIDk3Ljc5OSA3MTIuNyA5MC44OTkgNzEyLjcgNzcuNzk5QzcxMi43IDY5LjE5OSA3MDcuMyA2My4yOTkgNjk2LjMgNTkuNjk5TDY4OC44IDU3LjE5OUM2ODMuNyA1NS40OTkgNjgxLjMgNTMuMDk5IDY4MS4zIDQ5Ljc5OUM2ODEuMyA0NS45OTkgNjg0LjcgNDMuMzk5IDY4OS41IDQzLjM5OUM2OTQuMSA0My4zOTkgNjk4LjIgNDYuMzk5IDcwMC44IDUxLjU5OUw3MTEuOSA0NS41OTlDNzA3LjggMzcuMTk5IDY5OS4zIDMxLjk5OSA2ODkuNSAzMS45OTlDNjc3LjEgMzEuOTk5IDY2OC4xIDM5Ljk5OSA2NjguMSA1MC44OTlDNjY4LjEgNTkuNTk5IDY3My4zIDY1LjM5OSA2ODQgNjguNzk5TDY5MS43IDcxLjI5OUM2OTcuMSA3Mi45OTkgNjk5LjQgNzUuMTk5IDY5OS40IDc4LjY5OUM2OTkuNCA4My45OTkgNjk0LjUgODUuODk5IDY5MC4zIDg1Ljg5OUM2ODQuNyA4NS44OTkgNjc5LjcgODIuMjk5IDY3Ny4zIDc2LjM5OUw2NjYgODIuMzk5QzY2OS43IDkxLjg5OSA2NzguOCA5Ny43OTkgNjg5LjkgOTcuNzk5WiIgZmlsbD0id2hpdGUiPjwvcGF0aD4KPHBhdGggZD0iTTMxNC40IDk3LjE5OTRDMzE4LjggOTcuMTk5NCAzMjIuNyA5Ni43OTk0IDMyNC45IDk2LjQ5OTRWODQuNDk5NEMzMjMuMSA4NC42OTk0IDMxOS45IDg0Ljg5OTQgMzE4IDg0Ljg5OTRDMzEyLjQgODQuODk5NCAzMDguMSA4My44OTk0IDMwOC4xIDcxLjc5OTRWNDYuMDk5NEMzMDguMSA0NS4zOTk0IDMwOC42IDQ0Ljg5OTQgMzA5LjMgNDQuODk5NEgzMjIuOFYzMy41OTk0SDMwOS4zQzMwOC42IDMzLjU5OTQgMzA4LjEgMzMuMDk5NCAzMDguMSAzMi4zOTk0VjE0LjM5OTRIMjk0LjNWMzIuNDk5NEMyOTQuMyAzMy4xOTk0IDI5My44IDMzLjY5OTQgMjkzLjEgMzMuNjk5NEgyODMuNVY0NC45OTk0SDI5My4xQzI5My44IDQ0Ljk5OTQgMjk0LjMgNDUuNDk5NCAyOTQuMyA0Ni4xOTk0Vjc1LjI5OTRDMjk0LjMgOTcuMTk5NCAzMDguOSA5Ny4xOTk0IDMxNC40IDk3LjE5OTRaIiBmaWxsPSJ3aGl0ZSI+PC9wYXRoPgo8L2c+CjxkZWZzPgo8Y2xpcFBhdGggaWQ9ImNsaXAwXzE5ODRfNzMxIj4KPHJlY3Qgd2lkdGg9IjcxMi44IiBoZWlnaHQ9IjExMyIgZmlsbD0id2hpdGUiPjwvcmVjdD4KPC9jbGlwUGF0aD4KPC9kZWZzPgo8L3N2Zz4=" style={{ width: 230 }} /></Reveal>
            </div>
            <div style={{ position: 'absolute', left: 110, top: 300 }}>
              <Reveal lt={lt} at={0.5} dur={0.7}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                  <img src="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzAwIiBoZWlnaHQ9IjMzMSIgdmlld0JveD0iMCAwIDMwMCAzMzEiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik0yODMuOTIzIDEzNi40NDlMMTUwLjE0NCAyMTMuNjI0TDYuODg5OTUgMTMxLjE2OEwwIDEzNC45ODJWMTk0Ljg0NEwxNTAuMTQ0IDI4MS4xMTVMMjgzLjkyMyAyMDQuMjM0VjIzNS45MjZMMTUwLjE0NCAzMTMuMUw2Ljg4OTk1IDIzMC42NDRMMCAyMzQuNDU4VjI0NC43MjlMMTUwLjE0NCAzMzFMMzAwIDI0NC43MjlWMTg0Ljg2N0wyOTMuMTEgMTgxLjA1MkwxNTAuMTQ0IDI2My4yMTVMMTYuMDc2NiAxODYuMzM0VjE1NC42NDNMMTUwLjE0NCAyMzEuNTI0TDMwMCAxNDUuMjUzVjg2LjI3MTNMMjkyLjUzNiA4MS44Njk3TDE1MC4xNDQgMTYzLjczOUwyMi45NjY1IDkwLjk2NjNMMTUwLjE0NCAxNy44OTk4TDI1NC42NDEgNzguMDU1TDI2My44MjggNzIuNzczVjY1LjQzNzFMMTUwLjE0NCAwTDAgODYuMjcxM1Y5NS42NjEzTDE1MC4xNDQgMTgxLjkzM0wyODMuOTIzIDEwNC43NThWMTM2LjQ0OVoiIGZpbGw9IiNGRjM2MjEiPjwvcGF0aD4KPC9zdmc+" style={{ width: 56, height: 56 }} />
                  <span style={{ fontFamily: SANS, fontWeight: 700, fontSize: 40, color: C.white, letterSpacing: '-0.01em' }}>ProdLine Copilot</span>
                </div>
              </Reveal>
              <Reveal lt={lt} at={0.9} dur={0.7} style={{ marginTop: 24 }}>
                <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 74, color: C.white, letterSpacing: '-0.025em', lineHeight: 1.04 }}>See the line.<br/><span style={{ color: C.coral }}>Make the call.</span></div>
              </Reveal>
              <Reveal lt={lt} at={1.6} dur={0.7} style={{ marginTop: 30 }}>
                <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', maxWidth: 980 }}>
                  {points.map((p, i) => (
                    <Reveal key={i} lt={lt} at={1.8 + i * 0.22} dur={0.5} from="up" dist={12}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10, border: `1px solid rgba(255,255,255,.18)`, borderRadius: 999, padding: '11px 20px' }}>
                        <span style={{ width: 8, height: 8, borderRadius: 4, background: C.coral }}/>
                        <span style={{ fontFamily: SANS, fontWeight: 600, fontSize: 22, color: 'rgba(255,255,255,.92)' }}>{p}</span>
                      </div>
                    </Reveal>
                  ))}
                </div>
              </Reveal>
              <Reveal lt={lt} at={3.2} dur={0.7} style={{ marginTop: 40 }}>
                <div style={{ fontFamily: SANS, fontSize: 24, color: 'rgba(255,255,255,.6)' }}>Built on the Databricks Data Intelligence Platform.</div>
              </Reveal>
            </div>
          </div>
        );
      }}
    </Scene>
  );
}

function Movie(props) {
  const p = props || {};
  applyAccent(p.accent || 'lava');
  CFG.name = (p.supervisorName && String(p.supervisorName).trim()) || 'Maya';
  CFG.showClock = p.showClock !== false;
  const rate = (typeof p.playbackSpeed === 'number' && p.playbackSpeed > 0) ? p.playbackSpeed : 1;
  return (
    <Stage width={1920} height={1080} duration={110} background={C.oat} persistKey="prodline-copilot" rate={rate} chapters={CHAPTERS}>
      <Scene1 />
      <Scene2 />
      <SceneControlTower />
      <Scene3 />
      <CopilotAct />
      <Scene7 />
      <Scene8 />
    </Stage>
  );
}

if (typeof module !== 'undefined' && module.exports) { module.exports = { Movie }; }

Object.assign(window, {
  Easing, interpolate, animate, clamp,
  TimelineContext, useTime, useTimeline,
  Sprite, SpriteContext, useSprite,
  TextSprite, ImageSprite, RectSprite,
  Stage, PlaybackBar, Movie,
});

