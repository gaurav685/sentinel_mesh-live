import { severityOf, type Detection } from "@/lib/api";

export function SeverityBadge({
  detection,
}: {
  detection: Pick<Detection, "is_anomaly" | "normalized_score">;
}) {
  const { label, colorVar } = severityOf(detection);
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium capitalize"
      style={{
        color: colorVar,
        borderColor: colorVar,
        backgroundColor: `color-mix(in srgb, ${colorVar} 12%, transparent)`,
      }}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: colorVar }}
      />
      {label}
    </span>
  );
}
