"use client";

import { useEffect, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";

export interface GraphNode {
  id: string;
  label: string;
  color: string;
  isArchived?: boolean;
}

export interface GraphLink {
  source: string;
  target: string;
  isNew: boolean;
}

/** The centerpiece visual: incident memories as nodes, `linked_to` tags
 * (app/detection/consumer.py's real linking mechanism -- not a graph
 * edge on the backend, parsed client-side from `tags`) as edges. A
 * newly-appeared edge (the backend just linked a recurrence) is drawn in
 * the accent color with particles flowing along it; everything else is a
 * calm, fixed line -- the animation should read as "this just happened,"
 * not as constant background noise.
 *
 * Real bug fixed here: `ForceGraph2D` with no explicit `width` relies on
 * auto-detecting its container's size via `react-kapsule`'s internal
 * resize handling, which can race with the dashboard's own layout
 * (flex/grid parents, a dynamic import mounting after initial layout) and
 * silently produce a 0-width canvas -- nodes technically "render" at
 * (0,0)-ish coordinates with no visible simulation space to spread into,
 * which looks exactly like "isolated nodes, no edges" even when the edge
 * data is correct. Fixed by measuring the container explicitly with
 * ResizeObserver and passing a real pixel width, instead of trusting
 * auto-sizing. */
export function IncidentGraph({
  nodes,
  links,
  onNodeClick,
  height = 420,
}: {
  nodes: GraphNode[];
  links: GraphLink[];
  onNodeClick?: (id: string) => void;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w && w > 0) setWidth(Math.floor(w));
    });
    observer.observe(el);
    setWidth(el.getBoundingClientRect().width || null);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    // Diagnostic log, deliberately left in: the graph rendering "nothing
    // connected" is indistinguishable from "no link data was ever passed
    // in" from a screenshot alone. This makes the actual received data
    // inspectable in the browser console instead of guessed at.
    console.debug("[IncidentGraph] render", {
      width,
      nodeCount: nodes.length,
      linkCount: links.length,
      nodes,
      links,
    });
  }, [width, nodes, links]);

  return (
    <div ref={containerRef} style={{ width: "100%", height }}>
      {width !== null && (
        <ForceGraph2D
          graphData={{ nodes, links }}
          width={width}
          height={height}
          backgroundColor="transparent"
          nodeId="id"
          nodeLabel={(n) => (n as GraphNode).label}
          nodeRelSize={5}
          nodeColor={(n) => (n as GraphNode).color}
          nodeCanvasObjectMode={() => "after"}
          nodeCanvasObject={(node, ctx, globalScale) => {
            const n = node as GraphNode & { x?: number; y?: number };
            if (n.x === undefined || n.y === undefined) return;
            const fontSize = 10 / globalScale;
            ctx.font = `${fontSize}px var(--font-mono, monospace)`;
            ctx.fillStyle = n.isArchived ? "#5b6b7f" : "#8b98a9";
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillText(n.label, n.x, n.y + 7);
          }}
          linkColor={(l) => ((l as GraphLink).isNew ? "#22d3ee" : "#2a3546")}
          linkWidth={(l) => ((l as GraphLink).isNew ? 2.5 : 1)}
          linkDirectionalParticles={(l) => ((l as GraphLink).isNew ? 4 : 0)}
          linkDirectionalParticleWidth={3}
          linkDirectionalParticleColor={() => "#22d3ee"}
          linkDirectionalParticleSpeed={0.006}
          onNodeClick={(n) => onNodeClick?.((n as GraphNode).id)}
          cooldownTicks={100}
        />
      )}
    </div>
  );
}
