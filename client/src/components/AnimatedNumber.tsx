/**
 * Animates numeric transitions for KPI-style values with a short eased tween.
 * Used by metric-heavy cards to reduce abrupt jumps between SSE ticks.
 * Uses one in-flight RAF per value change to avoid overlapping animations.
 */
import { useEffect, useRef, useState, memo } from 'react';

interface AnimatedNumberProps {
  value: number;
  duration?: number;
  decimals?: number;
  className?: string;
  suffix?: string;
  prefix?: string;
}

/**
 * Smoothly tweens from the previous value to the new value.
 * Used for KPI numbers, throughput, speed_pct etc. to avoid flicker.
 */
function AnimatedNumberInner({
  value,
  duration = 400,
  decimals = 0,
  className,
  suffix = '',
  prefix = '',
}: AnimatedNumberProps) {
  const [display, setDisplay] = useState(value);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (display === value) return;

    const from = display;
    const to = value;
    const startTime = performance.now();
    const tick = (now: number) => {
      const elapsed = now - startTime;
      // why: keep interpolation clamped so delayed RAF frames never overshoot target values.
      const t = Math.min(elapsed / duration, 1);
      const eased = 1 - (1 - t) * (1 - t); // ease-out quad
      const current = from + (to - from) * eased;
      setDisplay(current);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setDisplay(to);
      }
    };

    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);

    return () => cancelAnimationFrame(rafRef.current);
  }, [value, duration]); // eslint-disable-line react-hooks/exhaustive-deps

  const formatted = decimals > 0 ? display.toFixed(decimals) : Math.round(display).toLocaleString();

  return (
    <span className={className}>
      {prefix}
      {formatted}
      {suffix}
    </span>
  );
}

const AnimatedNumber = memo(AnimatedNumberInner);
export default AnimatedNumber;
