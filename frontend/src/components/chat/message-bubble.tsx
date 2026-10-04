"use client";

import { CircleAlert, ClipboardCheck } from "lucide-react";

import { Button } from "@/components/ui/button";
import { VizView } from "@/components/viz/viz-view";
import type { AssistantTurn, WritePreview } from "@/lib/chat";
import { cn } from "@/lib/utils";

export type ChatItem =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; turn: AssistantTurn }
  | { id: string; kind: "error"; text: string };

const htmlLang = (lang: string) => (lang === "hi" ? "hi" : "en");

/** The confirm card for a write: exactly what will change. Nothing is saved before Confirm. */
function PreviewCard({ previews }: { previews: WritePreview[] }) {
  return (
    <div className="grid gap-3">
      {previews.map((p, i) => (
        <div key={i} className="rounded-lg border bg-muted/40 p-3">
          <p className="flex items-start gap-2 font-medium">
            <ClipboardCheck className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
            {p.summary}
          </p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {p.fields.map((f) => (
              <div key={f.label} className="contents">
                <dt className="text-muted-foreground">{f.label}</dt>
                <dd>{f.value}</dd>
              </div>
            ))}
          </dl>
          {p.effects.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-sm text-muted-foreground">
              {p.effects.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

export function MessageBubble({
  item,
  onChoose,
  active,
}: {
  item: ChatItem;
  onChoose: (text: string, kind: "resume" | "send") => void;
  active: boolean; // only the latest answer's buttons can be used
}) {
  if (item.kind === "user") {
    return (
      <div className="ml-auto max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
        {item.text}
      </div>
    );
  }
  if (item.kind === "error") {
    return (
      <div role="alert" className="flex max-w-[85%] items-start gap-2 rounded-2xl border border-destructive/30 px-4 py-2 text-sm text-destructive">
        <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        {item.text}
      </div>
    );
  }
  const { turn } = item;
  const clarify = turn.interrupt !== null;
  const choices = clarify ? turn.interrupt?.options ?? turn.suggestions : turn.suggestions;
  return (
    <div className="grid max-w-[92%] gap-3 rounded-2xl rounded-bl-sm border bg-card px-4 py-3 text-sm" lang={htmlLang(turn.lang)}>
      <p className="whitespace-pre-wrap leading-relaxed">{turn.answer}</p>
      {turn.interrupt?.kind === "confirm" && turn.interrupt.previews && (
        <PreviewCard previews={turn.interrupt.previews} />
      )}
      {!clarify && <VizView viz={turn.viz} columns={turn.columns} rows={turn.rows} rowCount={turn.row_count} />}
      {choices.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {choices.map((choice) => (
            <Button
              key={choice}
              variant={turn.interrupt?.kind === "confirm" && choice === choices[0] ? "default" : "outline"}
              size="sm"
              disabled={!active}
              className={cn("h-auto whitespace-normal py-1 text-left", !active && "opacity-60")}
              onClick={() => onChoose(choice, clarify ? "resume" : "send")}
            >
              {choice}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
