"use client";

import { Loader2, MessageSquarePlus, SendHorizontal } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { MessageBubble, type ChatItem } from "@/components/chat/message-bubble";
import { MicButton } from "@/components/chat/mic-button";
import { useSession } from "@/components/session-provider";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { chat, SAFE_ERROR, toTurn, type ChatEvent, type ThreadSummary } from "@/lib/chat";
import type { Role } from "@/lib/roles";
import { cn } from "@/lib/utils";
import { appendTranscript } from "@/lib/voice";

const MAX_MESSAGE = 2000; // backend/app/api/routes/chat.py

// Starter questions per role, worded as in the golden corpus so they also work offline.
const STARTERS: Record<Role, string[]> = {
  inventory_manager: [
    "How many MS bush OD40 do we have, and where?",
    "Give me a pie chart of items in stock by warehouse",
    "132-1 में कितने आइटम का स्टॉक शून्य है?",
  ],
  procurement_manager: [
    "Which purchase orders are overdue?",
    "What's the split of purchase orders by status?",
    "Sterling Steels ke kitne PO abhi pending hain?",
  ],
  owner: [
    "What were our profit margins for the last 3 quarters?",
    "Show the monthly procurement spend trend",
    "Explain our purchase order lifecycle",
  ],
};

function lastUserText(items: ChatItem[]): string {
  const item = items.findLast((i) => i.kind === "user");
  return item?.kind === "user" ? item.text : "";
}

// Unique across hot reloads and remounts: a module counter restarted at 0 on Fast Refresh while
// the messages stayed, giving two bubbles the key "local-8" (user report, 2026-10-05).
const nextId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? `local-${crypto.randomUUID()}`
    : `local-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

export function ChatView() {
  const { user } = useSession();
  const router = useRouter();
  const urlThread = useSearchParams().get("thread");
  const [threadId, setThreadId] = useState<string | null>(urlThread);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const threadRef = useRef<string | null>(urlThread); // the thread this view is showing

  const last = items.at(-1);
  const waitingForAnswer = last?.kind === "assistant" && last.turn.interrupt !== null;

  const refreshThreads = useCallback(() => {
    chat.threads().then(setThreads, () => undefined);
  }, []);

  useEffect(refreshThreads, [refreshThreads]);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }), [items, status]);

  // Open a conversation from the URL (sidebar click, reload, shared link to your own thread).
  useEffect(() => {
    if (urlThread === threadRef.current && (items.length || !urlThread)) return;
    threadRef.current = urlThread;
    setThreadId(urlThread);
    setItems([]);
    setLoadError(null);
    if (!urlThread) return;
    chat.messages(urlThread).then(
      (messages) =>
        setItems(
          messages.map((m) =>
            m.role === "user"
              ? { id: m.id, kind: "user", text: m.content }
              : { id: m.id, kind: "assistant", turn: toTurn(m.content, m.payload) },
          ),
        ),
      (err: unknown) =>
        setLoadError(err instanceof ApiError && err.status === 404 ? "Conversation not found." : SAFE_ERROR),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlThread]);

  function onEvent(event: ChatEvent) {
    if (event.type === "thread") {
      if (threadRef.current !== event.threadId) {
        threadRef.current = event.threadId;
        setThreadId(event.threadId);
        router.replace(`/chat?thread=${event.threadId}`, { scroll: false });
      }
    } else if (event.type === "status") {
      setStatus(event.label);
    } else if (event.type === "error") {
      setItems((prev) => [...prev, { id: nextId(), kind: "error", text: event.message }]);
    } else {
      setItems((prev) => [...prev, { id: nextId(), kind: "assistant", turn: event.turn }]);
    }
  }

  async function submit(text: string, mode: "send" | "resume") {
    const message = text.trim();
    if (!message || busy) return;
    setItems((prev) => [...prev, { id: nextId(), kind: "user", text: message }]);
    setDraft("");
    setBusy(true);
    setStatus("Understanding your question");
    abort.current = new AbortController();
    try {
      if (mode === "resume" && threadId) await chat.resume(threadId, message, onEvent, abort.current.signal);
      else await chat.send(message, threadId, onEvent, abort.current.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        const text = err instanceof ApiError ? err.message : SAFE_ERROR;
        setItems((prev) => [...prev, { id: nextId(), kind: "error", text }]);
      }
    } finally {
      setBusy(false);
      setStatus(null);
      refreshThreads();
    }
  }

  function newChat() {
    abort.current?.abort();
    router.push("/chat");
  }

  return (
    <div className="grid h-[calc(100dvh-8rem)] grid-cols-1 gap-4 md:grid-cols-[15rem_1fr]">
      <aside className="hidden min-h-0 flex-col gap-2 md:flex" aria-label="Conversations">
        <Button variant="outline" onClick={newChat} className="justify-start">
          <MessageSquarePlus aria-hidden />
          New conversation
        </Button>
        <nav className="min-h-0 flex-1 overflow-y-auto">
          {threads.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => router.push(`/chat?thread=${t.id}`)}
              aria-current={t.id === threadId ? "page" : undefined}
              className={cn(
                "block w-full truncate rounded-md px-3 py-2 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground",
                t.id === threadId && "bg-muted font-medium text-foreground",
              )}
              title={t.title}
            >
              {t.title}
            </button>
          ))}
        </nav>
      </aside>

      <section className="flex min-h-0 flex-col rounded-xl border bg-background">
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4" aria-live="polite">
          {loadError && <p className="text-sm text-destructive">{loadError}</p>}
          {!items.length && !loadError && (
            <div className="m-auto grid max-w-md gap-3 text-center">
              <h1 className="text-lg font-semibold">Ask about your data</h1>
              <p className="text-sm text-muted-foreground">
                English, हिंदी or Hinglish. Answers come only from your records, with a chart when it helps.
              </p>
              <div className="grid gap-2">
                {STARTERS[user.role].map((q) => (
                  <Button key={q} variant="outline" className="h-auto whitespace-normal py-2" onClick={() => submit(q, "send")}>
                    {q}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {items.map((item, i) => (
            <MessageBubble
              key={item.id}
              item={item}
              active={!busy && i === items.length - 1}
              onChoose={(text, kind) => submit(text, kind)}
            />
          ))}
          {status && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
              <Loader2 className="size-4 animate-spin" aria-hidden />
              {status}…
            </div>
          )}
          <div ref={bottom} />
        </div>

        {voiceError && (
          <p className="border-t px-3 pt-2 text-sm text-destructive" role="alert">
            {voiceError}
          </p>
        )}
        <form
          className="flex items-end gap-2 border-t p-3"
          onSubmit={(e) => {
            e.preventDefault();
            submit(draft, waitingForAnswer ? "resume" : "send");
          }}
        >
          <Textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setVoiceError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                submit(draft, waitingForAnswer ? "resume" : "send");
              }
            }}
            maxLength={waitingForAnswer ? 200 : MAX_MESSAGE}
            rows={1}
            placeholder={
              !waitingForAnswer
                ? "Ask a question or make a change…"
                : last?.kind === "assistant" && last.turn.suggestions.length
                  ? "Pick an option above or type your answer…"
                  : "Type your answer…"
            }
            aria-label="Message"
            className="max-h-40 min-h-10 resize-none"
            disabled={busy}
          />
          <MicButton
            disabled={busy}
            recentText={`${draft} ${lastUserText(items)}`}
            onText={(text) => {
              setVoiceError(null);
              setDraft((current) => appendTranscript(current, text)); // check it, then send
            }}
            onError={setVoiceError}
          />
          <Button type="submit" size="icon-lg" disabled={busy || !draft.trim()} aria-label="Send">
            <SendHorizontal aria-hidden />
          </Button>
        </form>
      </section>
    </div>
  );
}
