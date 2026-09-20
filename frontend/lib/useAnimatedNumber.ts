import { useEffect, useRef, useState } from "react";
import { animate } from "framer-motion";

/** Tweens a displayed number toward `value` instead of snapping -- used
 * for the dashboard's stat tiles so a poll-driven count change reads as
 * motion, not a flicker. */
export function useAnimatedNumber(value: number, duration = 0.6): number {
  const [display, setDisplay] = useState(value);
  const prev = useRef(value);

  useEffect(() => {
    const controls = animate(prev.current, value, {
      duration,
      ease: "easeOut",
      onUpdate: (v) => setDisplay(v),
    });
    prev.current = value;
    return () => controls.stop();
  }, [value, duration]);

  return display;
}
