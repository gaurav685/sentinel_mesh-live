"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  label: string;
  minHeight?: number;
}
interface State {
  error: Error | null;
}

/** Generic version of what was originally a graph-only boundary
 * (GraphErrorBoundary). Proven necessary a second time in this same
 * build: `ThreatScoreBar` crashed on an undefined prop during a real
 * version-skew window (frontend polling a backend that hadn't yet added
 * `threat_score`), and with no boundary around the alert feed, that
 * single bad prop would have unmounted the entire dashboard -- stat
 * tiles, graph, everything -- not just the one row. One bad field should
 * never be able to blank the whole page; every independent dashboard
 * section gets its own boundary now, not just the graph. */
export class SectionErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    console.error(`[${this.props.label}] crashed:`, error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          style={{ minHeight: this.props.minHeight }}
          className="flex flex-col items-center justify-center gap-2 p-6 text-sm text-severity-critical"
        >
          <div>
            {this.props.label} crashed: {this.state.error.message}
          </div>
          <div className="text-xs text-text-dim">See browser console for the full stack.</div>
        </div>
      );
    }
    return this.props.children;
  }
}
