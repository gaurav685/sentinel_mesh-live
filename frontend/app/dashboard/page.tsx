"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import {
  listDetections,
  listMemories,
  getStats,
  linkedIds,
  DEMO_TENANT_ID,
  type Detection,
  type MemoryRecord,
  type Stats,
} from "@/lib/api";
import { StatTile, StatPanel } from "@/components/StatTile";
import { SeverityBadge } from "@/components/SeverityBadge";
import { SectionErrorBoundary } from "@/components/SectionErrorBoundary";
import { TechniqueBadge } from "@/components/TechniqueBadge";
import { ThreatScoreBar } from "@/components/ThreatScoreBar";
import { PageTransition } from "@/components/PageTransition";
import type { GraphNode, GraphLink } from "@/components/IncidentGraph";

// react-force-graph-2d touches window/canvas directly -- never server-render it.
const IncidentGraph = dynamic(
  () => import("@/components/IncidentGraph").then((m) => m.IncidentGraph),
  { ssr: false }
);

const POLL_MS = 3000;
const NEW_EDGE_HIGHLIGHT_MS = 6000;

function edgeKey(a: string, b: string): string {
  return [a, b].sort().join("::");
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats>({ total_events: 0, anomaly_count: 0, memory_count: 0 });
  const [detections, setDetections] = useState<Detection[]>([]);
  const [incidents, setIncidents] = useState<MemoryRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newEdgeKeys, setNewEdgeKeys] = useState<Set<string>>(new Set());
  const knownEdges = useRef<Set<string>>(new Set());

  const poll = useCallback(async () => {
    try {
      const [s, d, m] = await Promise.all([
        getStats(),
        listDetections({ limit: 30 }),
        // includeArchived: true is deliberate, not a leftover default --
        // consolidation.py's dedup job archives a near-duplicate incident
        // but the surviving `linked_to` tag can live on either side of
        // the merge. Excluding archived incidents here was the actual
        // root cause of edges silently not rendering: the one node
        // carrying the `linked_to` tag was never even fetched.
        listMemories({ memoryType: "incident", limit: 100, includeArchived: true }),
      ]);
      setStats(s);
      setDetections(d);
      setIncidents(m);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  // Detect newly-appeared `linked_to` edges each poll and flag them for a
  // temporary highlight -- this is the moment a real recurrence linkage
  // becomes visible, not decoration.
  useEffect(() => {
    const current = new Set<string>();
    for (const m of incidents) {
      for (const targetId of linkedIds(m.tags)) {
        current.add(edgeKey(m.id, targetId));
      }
    }
    const fresh = [...current].filter((k) => !knownEdges.current.has(k));
    if (fresh.length > 0) {
      setNewEdgeKeys((prev) => new Set([...prev, ...fresh]));
      for (const k of fresh) {
        setTimeout(() => {
          setNewEdgeKeys((prev) => {
            const next = new Set(prev);
            next.delete(k);
            return next;
          });
        }, NEW_EDGE_HIGHLIGHT_MS);
      }
    }
    knownEdges.current = current;
  }, [incidents]);

  const incidentIds = new Set(incidents.map((m) => m.id));
  const targetedIds = new Set(incidents.flatMap((m) => linkedIds(m.tags)));

  const nodes: GraphNode[] = incidents.map((m) => ({
    id: m.id,
    label: m.id.slice(0, 8),
    isArchived: m.is_archived,
    color: m.is_archived
      ? "#3a4657" // merged away by consolidation -- still shown so its real linked_to edge is visible
      : targetedIds.has(m.id) || linkedIds(m.tags).length > 0
        ? "#a78bfa"
        : "#22d3ee",
  }));

  const links: GraphLink[] = incidents.flatMap((m) =>
    linkedIds(m.tags)
      .filter((targetId) => incidentIds.has(targetId))
      .map((targetId) => ({
        source: m.id,
        target: targetId,
        isNew: newEdgeKeys.has(edgeKey(m.id, targetId)),
      }))
  );

  return (
    <PageTransition>
    <div className="mx-auto max-w-7xl space-y-8 px-6 py-8">
      <div>
        <h1 className="text-xl font-semibold text-text">Live threat detection</h1>
        <p className="mt-1 text-sm text-text-muted">
          Polling the real backend every {POLL_MS / 1000}s · tenant{" "}
          <code className="text-text-dim">{DEMO_TENANT_ID}</code>
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-severity-critical/40 bg-severity-critical/10 px-4 py-3 text-sm text-severity-critical">
          Could not reach backend at API base URL: {error}
        </div>
      )}

      <StatPanel>
        <StatTile label="Total events" value={stats.total_events} />
        <StatTile label="Anomalies" value={stats.anomaly_count} accent="var(--severity-high)" />
        <StatTile label="Memories" value={stats.memory_count} accent="var(--color-accent)" />
      </StatPanel>

      <div className="rounded-xl border border-border bg-bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text">Incident relationship graph</h2>
          <span className="text-xs text-text-muted">
            {incidents.length} incidents ({incidents.filter((m) => m.is_archived).length} merged)
          </span>
        </div>
        {incidents.length === 0 ? (
          <div className="flex h-[420px] items-center justify-center text-sm text-text-dim">
            No incidents yet — run replay.main to generate real detections.
          </div>
        ) : (
          <SectionErrorBoundary label="Relationship graph" minHeight={420}>
            <IncidentGraph
              nodes={nodes}
              links={links}
              onNodeClick={(id) => {
                window.location.href = `/incident/${id}`;
              }}
            />
          </SectionErrorBoundary>
        )}
      </div>

      <div className="rounded-xl border border-border bg-bg-panel">
        <div className="border-b border-border-subtle px-4 py-3">
          <h2 className="text-sm font-semibold text-text">Alert feed</h2>
        </div>
        <SectionErrorBoundary label="Alert feed" minHeight={120}>
        <div className="divide-y divide-border-subtle">
          <AnimatePresence initial={false}>
            {detections.map((d) => (
              <motion.div
                key={d.id}
                layout
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.25 }}
                className="flex items-center justify-between gap-4 px-4 py-3"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <SeverityBadge detection={d} />
                  <div className="min-w-0">
                    <div className="truncate text-sm text-text">
                      {d.raw_event.protocol}/{d.raw_event.app_protocol} · {d.raw_event.src_ip} →{" "}
                      {d.raw_event.dst_ip}
                    </div>
                    <div className="mono-tabular text-xs text-text-dim">
                      score={d.normalized_score.toFixed(2)} · bytes_sent={d.raw_event.bytes_sent} ·{" "}
                      {new Date(d.created_at).toLocaleTimeString()}
                    </div>
                    {d.technique && (
                      <div className="mt-1">
                        <TechniqueBadge technique={d.technique} />
                      </div>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4">
                  {d.is_anomaly && <ThreatScoreBar score={d.threat_score} compact />}
                  {d.memory_id ? (
                    <Link
                      href={`/incident/${d.memory_id}`}
                      className="text-xs font-medium text-accent hover:underline"
                    >
                      View incident →
                    </Link>
                  ) : (
                    <span className="text-xs text-text-dim">no incident</span>
                  )}
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
          {detections.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-text-dim">No events yet.</div>
          )}
        </div>
        </SectionErrorBoundary>
      </div>
    </div>
    </PageTransition>
  );
}
