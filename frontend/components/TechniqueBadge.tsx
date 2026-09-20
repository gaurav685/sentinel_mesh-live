import type { Technique } from "@/lib/api";

/** Labeled explicitly as heuristic everywhere it appears -- the mapping
 * (`app/mitre/catalog.py`) never sees NSL-KDD's own attack label, only
 * the same features the detection pipeline sees, so it can and sometimes
 * will disagree with the dataset's hidden ground truth. That's the
 * correct, honest behavior of a feature-only rule, not a defect. */
export function TechniqueBadge({ technique }: { technique: Technique | null }) {
  if (!technique) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle px-2.5 py-1 text-xs text-text-dim">
        No confident MITRE mapping
      </span>
    );
  }
  return (
    <div className="inline-flex flex-col gap-1">
      <span
        className="inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium"
        style={{ color: "var(--severity-linked)", borderColor: "var(--severity-linked)" }}
        title={technique.reason}
      >
        {technique.id} · {technique.name}
        <span className="text-text-dim">({technique.tactic})</span>
      </span>
      <span className="text-[11px] text-text-dim">
        Rule-based heuristic mapping, not a certified classification — {technique.reason}
      </span>
    </div>
  );
}
