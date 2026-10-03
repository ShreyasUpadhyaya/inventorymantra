// Chat API client (backend/app/api/routes/chat.py). A turn streams:
//   thread → status* → answer | clarify | error

import { apiFetch, apiJson } from "./api";
import { readSSE } from "./sse";

export type VizType = "none" | "kpi" | "table" | "bar" | "line" | "pie" | "stacked_bar" | "flow";

export type VizSpec = {
  type: VizType;
  title: string;
  x?: string | null;
  y?: string[];
  mermaid?: string | null;
};

export type Cell = string | number | boolean | null;

/** What the UI needs to draw one assistant turn (live or from history). */
export type AssistantTurn = {
  route: string;
  lang: string;
  answer: string;
  suggestions: string[];
  interrupt: { kind: string; question: string; options: string[] } | null;
  viz: VizSpec | null;
  columns: string[];
  rows: Cell[][];
  row_count: number;
  failure: string | null;
};

export type ThreadSummary = { id: string; title: string; created_at: string; updated_at: string };

export type StoredMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  payload: Partial<AssistantTurn>;
  created_at: string;
};

export type ChatEvent =
  | { type: "thread"; threadId: string }
  | { type: "status"; label: string }
  | { type: "answer" | "clarify"; turn: AssistantTurn }
  | { type: "error"; message: string };

export const SAFE_ERROR = "Something went wrong while answering. Please try again.";

export function toTurn(answer: string, payload: Partial<AssistantTurn>): AssistantTurn {
  return {
    route: payload.route ?? "read",
    lang: payload.lang ?? "en",
    answer,
    suggestions: payload.suggestions ?? [],
    interrupt: payload.interrupt ?? null,
    viz: payload.viz ?? null,
    columns: payload.columns ?? [],
    rows: payload.rows ?? [],
    row_count: payload.row_count ?? 0,
    failure: payload.failure ?? null,
  };
}

export function parseEvent(event: string, data: string): ChatEvent | null {
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(data) as Record<string, unknown>;
  } catch {
    return null;
  }
  switch (event) {
    case "thread":
      return { type: "thread", threadId: String(body.thread_id) };
    case "status":
      return { type: "status", label: String(body.label) };
    case "answer":
    case "clarify":
      return { type: event, turn: toTurn(String(body.answer ?? ""), body as Partial<AssistantTurn>) };
    case "error":
      return { type: "error", message: String(body.message ?? SAFE_ERROR) };
    default:
      return null;
  }
}

async function stream(path: string, body: object, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) {
  const response = await apiFetch(path, {
    method: "POST",
    body: JSON.stringify(body),
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.body) throw new Error("No response stream");
  await readSSE(response.body, ({ event, data }) => {
    const parsed = parseEvent(event, data);
    if (parsed) onEvent(parsed);
  });
}

export const chat = {
  send: (message: string, threadId: string | null, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) =>
    stream("/chat", threadId ? { message, thread_id: threadId } : { message }, onEvent, signal),
  resume: (threadId: string, answer: string, onEvent: (e: ChatEvent) => void, signal?: AbortSignal) =>
    stream(`/chat/${threadId}/resume`, { answer }, onEvent, signal),
  threads: () => apiJson<ThreadSummary[]>("/chat/threads"),
  messages: (threadId: string) => apiJson<StoredMessage[]>(`/chat/threads/${threadId}`),
};
