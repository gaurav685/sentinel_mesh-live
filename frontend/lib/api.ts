// Talks to the real SentinelMesh Live backend -- no mock data anywhere in
// this module. Tenant scoping is the same placeholder gap the backend
// itself states (app/memory/routes.py's `get_tenant_id` docstring): a
// plain header, not a verified session. NEXT_PUBLIC_DEMO_TENANT_ID must
// match whatever `--tenant-id` replay/main.py was run with (both default
// to the same well-known placeholder UUID when neither is overridden).

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const DEMO_TENANT_ID =
  process.env.NEXT_PUBLIC_DEMO_TENANT_ID ??
  "00000000-0000-0000-0000-000000000001";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Tenant-Id": DEMO_TENANT_ID,
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${path} -> HTTP ${res.status}: ${detail.slice(0, 300)}`);
  }
  return (await res.json()) as T;
}

export interface RawEventSummary {
  source_type: string;
  src_ip: string | null;
  dst_ip: string | null;
  protocol: string | null;
  app_protocol: string | null;
  bytes_sent: number | null;
  bytes_received: number | null;
  verdict: string | null;
}

export interface Technique {
  id: string;
  name: string;
  tactic: string;
  reason: string;
}

export interface Detection {
  id: string;
  created_at: string;
  detector: string;
  model_version: string;
  score: number;
  normalized_score: number;
  threshold: number;
  is_anomaly: boolean;
  memory_id: string | null;
  feature_values: Record<string, number>;
  raw_event: RawEventSummary;
  technique: Technique | null;
  chain_length: number;
  threat_score: number;
}

export interface Stats {
  total_events: number;
  anomaly_count: number;
  memory_count: number;
}

export interface MemoryRecord {
  id: string;
  content: string;
  memory_type: string;
  importance_score: number;
  emotional_score: number;
  retrieval_count: number;
  is_archived: boolean;
  is_poisoned: boolean;
  tags: string[];
  created_at: string;
  last_retrieved_at: string | null;
}

export interface ChatMatch {
  memory_id: string;
  content: string;
  created_at: string;
  composite_score: number;
  tags: string[];
}

export interface ChatResponse {
  query: string;
  answer: string;
  matches: ChatMatch[];
  grounded: boolean;
}

export interface ChainMemory {
  id: string;
  content: string;
  created_at: string;
  importance_score: number;
  is_archived: boolean;
  tags: string[];
}

export interface Chain {
  chain_id: string;
  length: number;
  first_seen: string;
  last_seen: string;
  memories: ChainMemory[];
}

export interface ChainReport {
  chain_id: string;
  report: string;
  disclaimer: string;
  generated_at: string;
}

export function listDetections(params: { limit?: number; memoryId?: string } = {}) {
  const q = new URLSearchParams();
  q.set("limit", String(params.limit ?? 50));
  if (params.memoryId) q.set("memory_id", params.memoryId);
  return apiFetch<Detection[]>(`/api/v1/detections?${q.toString()}`);
}

export function getStats() {
  return apiFetch<Stats>(`/api/v1/stats`);
}

export function listMemories(
  params: { memoryType?: string; limit?: number; includeArchived?: boolean } = {}
) {
  const q = new URLSearchParams();
  if (params.memoryType) q.set("memory_type", params.memoryType);
  q.set("limit", String(params.limit ?? 100));
  if (params.includeArchived) q.set("include_archived", "true");
  return apiFetch<MemoryRecord[]>(`/api/v1/memory?${q.toString()}`);
}

export function getMemory(id: string) {
  return apiFetch<MemoryRecord>(`/api/v1/memory/${id}`);
}

export function postChat(query: string, topK = 5) {
  return apiFetch<ChatResponse>(`/api/chat`, {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
  });
}

export function listChains() {
  return apiFetch<Chain[]>(`/api/v1/chains`);
}

export function getChain(chainId: string) {
  return apiFetch<Chain>(`/api/v1/chains/${chainId}`);
}

export function generateChainReport(chainId: string) {
  return apiFetch<ChainReport>(`/api/v1/chains/${chainId}/report`, { method: "POST" });
}

/** Parses `linked_to:<uuid>` tag entries into plain target ids. */
export function linkedIds(tags: string[]): string[] {
  return tags
    .filter((t) => t.startsWith("linked_to:"))
    .map((t) => t.slice("linked_to:".length));
}

export function severityOf(d: Pick<Detection, "is_anomaly" | "normalized_score">): {
  label: string;
  colorVar: string;
} {
  if (!d.is_anomaly) return { label: "info", colorVar: "var(--severity-info)" };
  if (d.normalized_score >= 0.85) return { label: "critical", colorVar: "var(--severity-critical)" };
  if (d.normalized_score >= 0.7) return { label: "high", colorVar: "var(--severity-high)" };
  return { label: "medium", colorVar: "var(--severity-medium)" };
}

/** Same four-tier bands as `severityOf`, applied to the composite threat
 * score instead of raw model confidence -- one shared status vocabulary
 * across the dashboard, not two competing color scales for two numbers
 * that both mean "how bad is this." */
export function threatScoreColor(score: number): string {
  if (score >= 0.85) return "var(--severity-critical)";
  if (score >= 0.7) return "var(--severity-high)";
  if (score >= 0.5) return "var(--severity-medium)";
  return "var(--severity-info)";
}
