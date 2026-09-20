"use client";

import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { useAnimatedNumber } from "@/lib/useAnimatedNumber";

/** One slot in a shared stat panel (StatPanel below) -- no border/background
 * of its own. Three individually-boxed tiles read as three unrelated
 * numbers; one panel with internal dividers reads as one system, which is
 * what dashboard-class products (Datadog/Grafana) actually do. */
export function StatTile({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  const display = useAnimatedNumber(value);

  return (
    <div className="flex-1 px-5 py-4">
      <div className="text-xs font-medium uppercase tracking-wider text-text-muted">{label}</div>
      <motion.div
        layout
        className="mono-tabular mt-1 text-3xl font-semibold"
        style={{ color: accent ?? "var(--color-text)" }}
      >
        {Math.round(display).toLocaleString()}
      </motion.div>
    </div>
  );
}

export function StatPanel({ children }: { children: ReactNode }) {
  return (
    <div className="flex divide-x divide-border-subtle overflow-hidden rounded-xl border border-border bg-bg-panel">
      {children}
    </div>
  );
}
