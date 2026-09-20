import Link from "next/link";
import type { ReactNode } from "react";

const UUID_PATTERN = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

/** Turns any incident id the LLM cited in its answer text into a clickable
 * link to that incident's detail page -- the grounding the chat endpoint
 * already guarantees (real ids only, see app/chat/query.py) made visible
 * and actionable, not just printed. */
export function ChatAnswer({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  // A fresh RegExp per call, not a shared module-level one whose
  // `.lastIndex` gets reset -- a `g`-flagged regex is stateful, and a
  // shared instance would leak that state across calls/renders.
  const uuidRe = new RegExp(UUID_PATTERN, "gi");

  while ((match = uuidRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const id = match[0];
    parts.push(
      <Link
        key={`${id}-${match.index}`}
        href={`/incident/${id}`}
        className="mono-tabular rounded bg-accent/10 px-1 py-0.5 text-accent hover:underline"
      >
        {id}
      </Link>
    );
    lastIndex = match.index + id.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));

  return <p className="whitespace-pre-wrap text-sm leading-relaxed">{parts}</p>;
}
