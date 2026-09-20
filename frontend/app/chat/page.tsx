"use client";

import { useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { postChat, type ChatMatch } from "@/lib/api";
import { ChatAnswer } from "@/components/ChatAnswer";
import { PageTransition } from "@/components/PageTransition";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  matches?: ChatMatch[];
  error?: boolean;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const counter = useRef(0);

  async function send() {
    const query = input.trim();
    if (!query || pending) return;
    setInput("");
    const userId = `u${counter.current++}`;
    setMessages((prev) => [...prev, { id: userId, role: "user", text: query }]);
    setPending(true);

    try {
      const res = await postChat(query);
      setMessages((prev) => [
        ...prev,
        { id: `a${counter.current++}`, role: "assistant", text: res.answer, matches: res.matches },
      ]);
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: `a${counter.current++}`,
          role: "assistant",
          text: e instanceof Error ? e.message : String(e),
          error: true,
        },
      ]);
    } finally {
      setPending(false);
    }
  }

  return (
    <PageTransition>
    <div className="mx-auto flex h-[calc(100vh-3.5rem)] max-w-3xl flex-col px-6 py-8">
      <div>
        <h1 className="text-xl font-semibold text-text">Incident chat</h1>
        <p className="mt-1 text-sm text-text-muted">
          Grounded in the real memory store — answers cite real incident ids, or say plainly when
          nothing relevant was found.
        </p>
      </div>

      <div className="mt-6 flex-1 space-y-4 overflow-y-auto">
        <AnimatePresence initial={false}>
          {messages.map((m) => (
            <motion.div
              key={m.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
            >
              <div
                className={`max-w-[85%] rounded-xl border px-4 py-3 ${
                  m.role === "user"
                    ? "border-accent-dim/40 bg-accent/10"
                    : m.error
                      ? "border-severity-critical/40 bg-severity-critical/10"
                      : "border-border bg-bg-panel"
                }`}
              >
                {m.role === "user" ? (
                  <p className="text-sm text-text">{m.text}</p>
                ) : (
                  <>
                    <ChatAnswer text={m.text} />
                    {m.matches && m.matches.length > 0 && (
                      <div className="mt-3 space-y-1.5 border-t border-border-subtle pt-3">
                        <div className="text-xs font-medium uppercase tracking-wide text-text-muted">
                          Retrieved incidents
                        </div>
                        {m.matches.map((match) => (
                          <Link
                            key={match.memory_id}
                            href={`/incident/${match.memory_id}`}
                            className="block text-xs text-text-muted hover:text-accent"
                          >
                            <span className="mono-tabular text-accent">
                              {match.memory_id.slice(0, 8)}
                            </span>{" "}
                            · composite={match.composite_score.toFixed(2)} · {match.content.slice(0, 70)}
                            {match.content.length > 70 ? "…" : ""}
                          </Link>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
        {pending && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex justify-start">
            <div className="rounded-xl border border-border bg-bg-panel px-4 py-3 text-sm text-text-dim">
              Thinking…
            </div>
          </motion.div>
        )}
        {messages.length === 0 && (
          <div className="flex h-full items-center justify-center text-sm text-text-dim">
            Ask about a past incident, e.g. &quot;have we seen a smurf attack?&quot;
          </div>
        )}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className="mt-4 flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about a past incident…"
          className="flex-1 rounded-lg border border-border bg-bg-elevated px-4 py-2.5 text-sm text-text placeholder:text-text-dim focus:border-accent focus:outline-none"
        />
        <button
          type="submit"
          disabled={pending || !input.trim()}
          className="rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-bg disabled:opacity-40"
        >
          Send
        </button>
      </form>
    </div>
    </PageTransition>
  );
}
