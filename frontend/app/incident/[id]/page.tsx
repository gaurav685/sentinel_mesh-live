"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { getMemory, listDetections, linkedIds, type Detection, type MemoryRecord } from "@/lib/api";
import { TechniqueBadge } from "@/components/TechniqueBadge";
import { ThreatScoreBar } from "@/components/ThreatScoreBar";
import { PageTransition } from "@/components/PageTransition";

async function safeGetMemory(id: string): Promise<MemoryRecord | null> {
  try {
    return await getMemory(id);
  } catch {
    return null;
  }
}

export default function IncidentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [memory, setMemory] = useState<MemoryRecord | null | undefined>(undefined);
  const [detection, setDetection] = useState<Detection | null>(null);
  const [linked, setLinked] = useState<MemoryRecord[]>([]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const m = await safeGetMemory(id);
      if (cancelled) return;
      setMemory(m);
      if (!m) return;

      const [detections, linkedSummaries] = await Promise.all([
        listDetections({ memoryId: id, limit: 1 }),
        Promise.all(linkedIds(m.tags).map((linkedId) => safeGetMemory(linkedId))),
      ]);
      if (cancelled) return;
      setDetection(detections[0] ?? null);
      setLinked(linkedSummaries.filter((x): x is MemoryRecord => x !== null));
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (memory === undefined) {
    return <div className="px-6 py-8 text-sm text-text-dim">Loading…</div>;
  }
  if (memory === null) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-8">
        <Link href="/dashboard" className="text-sm text-text-muted hover:text-text">
          ← Back to dashboard
        </Link>
        <p className="mt-6 text-sm text-severity-critical">
          Incident {id} not found for this tenant.
        </p>
      </div>
    );
  }

  return (
    <PageTransition>
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-8">
      <div>
        <Link href="/dashboard" className="text-sm text-text-muted hover:text-text">
          ← Back to dashboard
        </Link>
      </div>

      <div className="rounded-xl border border-border bg-bg-panel p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="mono-tabular text-lg font-semibold text-text">{memory.id}</h1>
            <p className="mt-1 text-xs text-text-muted">
              {memory.memory_type} · created {new Date(memory.created_at).toLocaleString()}
              {memory.is_archived && " · archived (merged by consolidation)"}
            </p>
          </div>
          <div className="shrink-0 text-right">
            <div className="text-xs uppercase tracking-wide text-text-muted">Importance</div>
            <div className="mono-tabular text-2xl font-semibold text-accent">
              {memory.importance_score.toFixed(2)}
            </div>
          </div>
        </div>

        {detection && (
          <div className="mt-4 flex flex-wrap items-center gap-4 border-t border-border-subtle pt-4">
            <TechniqueBadge technique={detection.technique} />
            <ThreatScoreBar score={detection.threat_score} />
            {detection.chain_length > 1 && (
              <span className="rounded-full border border-severity-linked/40 px-2 py-0.5 text-xs text-severity-linked">
                chain of {detection.chain_length}
              </span>
            )}
          </div>
        )}

        <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-text">{memory.content}</p>

        {memory.tags.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2">
            {memory.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-border-subtle px-2 py-0.5 text-xs text-text-muted"
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      {detection && (
        <div className="rounded-xl border border-border bg-bg-panel p-6">
          <h2 className="text-sm font-semibold text-text">Detection</h2>
          <div className="mono-tabular mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <Stat label="Detector" value={detection.detector} />
            <Stat label="Model" value={detection.model_version} />
            <Stat label="Score" value={detection.score.toFixed(4)} />
            <Stat label="Normalized" value={detection.normalized_score.toFixed(2)} />
            <Stat label="Threshold" value={detection.threshold.toFixed(2)} />
            <Stat label="Anomaly" value={detection.is_anomaly ? "yes" : "no"} />
          </div>

          <h3 className="mt-5 text-xs font-semibold uppercase tracking-wide text-text-muted">
            Feature vector
          </h3>
          <div className="mono-tabular mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
            {Object.entries(detection.feature_values).map(([name, value]) => (
              <div key={name} className="flex justify-between gap-2 text-text-muted">
                <span className="truncate">{name}</span>
                <span className="text-text">{value.toFixed(3)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-xl border border-border bg-bg-panel p-6">
        <h2 className="text-sm font-semibold text-text">
          Linked past incidents {linked.length > 0 && `(${linked.length})`}
        </h2>
        {linked.length === 0 ? (
          <p className="mt-2 text-sm text-text-dim">
            No linked past incidents — this is the first occurrence of this signature (or no
            recurrence has crossed the linking threshold yet).
          </p>
        ) : (
          <ul className="mt-3 space-y-2">
            {linked.map((l) => (
              <li key={l.id}>
                <Link
                  href={`/incident/${l.id}`}
                  className="block rounded-lg border border-border-subtle px-4 py-3 transition-colors hover:border-accent/50 hover:bg-bg-elevated"
                >
                  <div className="mono-tabular text-sm text-accent">{l.id}</div>
                  <div className="mt-1 truncate text-xs text-text-muted">{l.content}</div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
    </PageTransition>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-text-muted">{label}</div>
      <div className="text-text">{value}</div>
    </div>
  );
}
