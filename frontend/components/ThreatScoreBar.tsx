"use client";

import { motion } from "framer-motion";
import { threatScoreColor } from "@/lib/api";

/** `threat_score` (app/detection/scoring.py's documented formula) shown
 * as a filled bar, not a bare number -- magnitude reads faster as length
 * than as digits, and the fill animates in rather than snapping so a
 * live-updating list doesn't flicker.
 *
 * Real bug fixed here: this crashed with "can't access property 'toFixed',
 * score is undefined" the moment the frontend polled a backend response
 * that didn't yet carry `threat_score` (a version-skew window during this
 * same build, before the backend picked up the field) -- and with no
 * error boundary around this component, that uncaught render error
 * unmounted the *entire* dashboard, not just this bar. Never trust a
 * numeric prop to actually be a finite number at render time. */
export function ThreatScoreBar({ score, compact = false }: { score: number | null | undefined; compact?: boolean }) {
  const safeScore = typeof score === "number" && Number.isFinite(score) ? Math.max(0, Math.min(1, score)) : null;

  if (safeScore === null) {
    return (
      <div className={compact ? "w-20" : "w-full max-w-xs"}>
        <div className="text-xs text-text-dim">Threat score unavailable</div>
      </div>
    );
  }

  const color = threatScoreColor(safeScore);
  return (
    <div className={compact ? "w-20" : "w-full max-w-xs"}>
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-muted">Threat score</span>
        <span className="mono-tabular font-medium" style={{ color }}>
          {safeScore.toFixed(2)}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-bg-elevated">
        <motion.div
          className="h-full rounded-full"
          style={{ backgroundColor: color }}
          initial={{ width: 0 }}
          animate={{ width: `${Math.round(safeScore * 100)}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
        />
      </div>
    </div>
  );
}
