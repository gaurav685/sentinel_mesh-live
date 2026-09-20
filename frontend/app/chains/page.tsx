"use client";

import { useCallback, useEffect, useState } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import { listChains, getChain, generateChainReport, type Chain, type ChainReport } from "@/lib/api";
import { PageTransition } from "@/components/PageTransition";

const POLL_MS = 5000;

export default function ChainsPage() {
  const [chains, setChains] = useState<Chain[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Chain | null>(null);
  const [report, setReport] = useState<ChainReport | null>(null);
  const [generating, setGenerating] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const poll = useCallback(async () => {
    try {
      const list = await listChains();
      setChains(list);
    } catch {
      // Dashboard already surfaces backend-connectivity errors prominently;
      // this page degrades to "no chains yet" rather than duplicating that.
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => clearInterval(id);
  }, [poll]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    setReport(null);
    setReportError(null);
    getChain(selectedId).then(setSelected).catch(() => setSelected(null));
  }, [selectedId]);

  async function onGenerateReport() {
    if (!selectedId) return;
    setGenerating(true);
    setReportError(null);
    try {
      const r = await generateChainReport(selectedId);
      setReport(r);
    } catch (e) {
      setReportError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  return (
    <PageTransition>
      <div className="mx-auto max-w-5xl space-y-8 px-6 py-8">
        <div>
          <h1 className="text-xl font-semibold text-text">Attack chains</h1>
          <p className="mt-1 text-sm text-text-muted">
            Incidents grouped by the real <code className="text-text-dim">linked_to</code> relationship —
            a chain of 2+ occurrences of the same signature.
          </p>
        </div>

        {chains.length === 0 ? (
          <div className="rounded-xl border border-border bg-bg-panel px-6 py-10 text-center text-sm text-text-dim">
            No chains yet — a chain forms once the detection consumer links a second occurrence of a
            signature to a past one.
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {chains.map((chain) => (
              <button
                key={chain.chain_id}
                onClick={() => setSelectedId(chain.chain_id)}
                className={`rounded-xl border px-4 py-3 text-left transition-colors ${
                  selectedId === chain.chain_id
                    ? "border-accent/60 bg-bg-elevated"
                    : "border-border bg-bg-panel hover:border-border-subtle"
                }`}
              >
                <div className="mono-tabular text-xs text-accent">{chain.chain_id.slice(0, 8)}…</div>
                <div className="mt-1 text-sm text-text">{chain.length} linked incidents</div>
                <div className="mt-1 text-xs text-text-dim">
                  {new Date(chain.first_seen).toLocaleString()} → {new Date(chain.last_seen).toLocaleString()}
                </div>
              </button>
            ))}
          </div>
        )}

        {selected && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-xl border border-border bg-bg-panel p-6"
          >
            <h2 className="text-sm font-semibold text-text">Timeline</h2>

            <div className="mt-6 overflow-x-auto pb-2">
              <div className="relative flex min-w-max gap-8 px-2">
                <div className="absolute left-4 right-4 top-[7px] h-px bg-border" />
                {selected.memories.map((memory) => (
                  <Link
                    key={memory.id}
                    href={`/incident/${memory.id}`}
                    className="relative flex w-56 flex-col gap-2"
                  >
                    <div
                      className="z-10 h-3.5 w-3.5 rounded-full border-2 border-bg-panel"
                      style={{ backgroundColor: memory.is_archived ? "#3a4657" : "var(--color-accent)" }}
                    />
                    <div className="text-xs text-text-dim">{new Date(memory.created_at).toLocaleString()}</div>
                    <div className="mono-tabular text-xs text-accent">{memory.id.slice(0, 8)}</div>
                    <div className="line-clamp-3 text-xs text-text-muted">{memory.content}</div>
                  </Link>
                ))}
              </div>
            </div>

            <div className="mt-6 border-t border-border-subtle pt-4">
              <button
                onClick={onGenerateReport}
                disabled={generating}
                className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-bg disabled:opacity-40"
              >
                {generating ? "Generating…" : "Generate report"}
              </button>

              {reportError && (
                <p className="mt-3 text-sm text-severity-critical">Could not generate report: {reportError}</p>
              )}

              {report && (
                <motion.div
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="mt-4 rounded-lg border border-severity-linked/40 bg-severity-linked/10 p-4"
                >
                  <div className="text-xs font-semibold uppercase tracking-wide text-severity-linked">
                    {report.disclaimer}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-text">{report.report}</p>
                </motion.div>
              )}
            </div>
          </motion.div>
        )}
      </div>
    </PageTransition>
  );
}
