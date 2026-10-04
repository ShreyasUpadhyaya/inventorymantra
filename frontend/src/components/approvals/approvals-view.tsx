"use client";

import { CircleAlert, CircleCheck, MessageCircleQuestion } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { useSession } from "@/components/session-provider";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { approvals, type ApprovalMessage, type PendingApproval } from "@/lib/approvals";
import { formatCell, formatDate } from "@/lib/format";
import { ago } from "@/lib/notifications";

type Mode = "approve" | "reject" | "ask" | "reply";
const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Something went wrong. Please try again.";

const PROMPTS: Record<Mode, { label: string; button: string; required: boolean }> = {
  approve: { label: "Approve and place this order? Note for the requester (optional)", button: "Yes, approve", required: false },
  reject: { label: "Reject this order? Note for the requester (optional)", button: "Yes, reject", required: false },
  ask: { label: "Your question for the requester. The order keeps waiting for your decision.", button: "Send question", required: true },
  reply: { label: "Your answer for the Owner", button: "Send reply", required: true },
};

function Thread({ messages }: { messages: ApprovalMessage[] }) {
  if (!messages.length) return null;
  return (
    <ol className="grid gap-2" aria-label="Questions and answers">
      {messages.map((m, i) => (
        <li
          key={i}
          className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
            m.kind === "question" ? "bg-muted" : "ml-auto bg-primary/10"
          }`}
        >
          <div className="text-xs text-muted-foreground">
            {m.author_name} · {ago(m.created_at)}
          </div>
          {m.body}
        </li>
      ))}
    </ol>
  );
}

function ApprovalCard({
  item,
  owner,
  onDone,
}: {
  item: PendingApproval;
  owner: boolean;
  onDone: (text: string) => void;
}) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const waiting = item.status === "pending";

  async function send(chosen: Mode) {
    if (PROMPTS[chosen].required && !text.trim()) {
      setError("Please write a message first.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result =
        chosen === "ask"
          ? await approvals.ask(item.po_number, text.trim())
          : chosen === "reply"
            ? await approvals.reply(item.po_number, text.trim())
            : await approvals.decide(item.po_number, chosen, text);
      setMode(null);
      setText("");
      onDone(result.message);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {item.po_number} · {formatCell(item.total_amount, "total_amount")}
          {!owner && (
            <Badge variant={waiting ? "secondary" : "outline"}>
              {waiting ? "Waiting for the Owner" : item.status === "approved" ? "Approved" : "Rejected"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          {item.vendor_name ?? "—"} · requested by {item.requested_by_name} on {formatDate(item.requested_at)}
          {item.expected_delivery_date && <> · expected {formatDate(item.expected_delivery_date)}</>}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                {["Item", "Quantity", "Unit price", "Line total"].map((h, i) => (
                  <th key={h} scope="col" className={`px-3 py-2 font-medium ${i ? "text-right" : "text-left"}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {item.lines.map((line) => (
                <tr key={line.item_name} className="border-t">
                  <td className="px-3 py-1.5">{line.item_name}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {formatCell(line.quantity)} {line.unit}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatCell(line.unit_price, "unit_price")}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{formatCell(line.line_total, "line_total")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Thread messages={item.messages} />
        {error && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {waiting && mode && (
          <div className="grid gap-2">
            <label htmlFor={`text-${item.po_number}`} className="text-sm font-medium">
              {PROMPTS[mode].label}
            </label>
            <Textarea
              id={`text-${item.po_number}`}
              value={text}
              maxLength={mode === "approve" || mode === "reject" ? 300 : 500}
              onChange={(e) => setText(e.target.value)}
              rows={2}
            />
            <div className="flex gap-2">
              <Button variant={mode === "reject" ? "destructive" : "default"} disabled={busy} onClick={() => send(mode)}>
                {busy ? "Saving…" : PROMPTS[mode].button}
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setMode(null)}>
                Back
              </Button>
            </div>
          </div>
        )}
        {waiting && !mode && (
          <div className="flex flex-wrap gap-2">
            {owner ? (
              <>
                <Button onClick={() => setMode("approve")}>Approve</Button>
                <Button variant="outline" onClick={() => setMode("reject")}>
                  Reject
                </Button>
                <Button variant="outline" onClick={() => setMode("ask")}>
                  <MessageCircleQuestion aria-hidden />
                  Ask {item.requested_by_name.split(" ")[0]}
                </Button>
              </>
            ) : (
              <Button variant="outline" onClick={() => setMode("reply")}>
                Reply to the Owner
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ApprovalsView() {
  const { user } = useSession();
  const owner = user.role === "owner";
  const [items, setItems] = useState<PendingApproval[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    (owner ? approvals.pending() : approvals.mine()).then(setItems, (err: unknown) => setError(message(err)));
  }, [owner]);
  useEffect(load, [load]);

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-xl font-semibold">{owner ? "Approvals" : "My approval requests"}</h1>
        <p className="text-sm text-muted-foreground">
          {owner
            ? "Purchase orders over the approval limit wait here until you decide. You can ask the requester first."
            : "Your purchase orders over the approval limit, and any questions from the Owner."}
        </p>
      </div>
      {done && (
        <Alert>
          <CircleCheck aria-hidden />
          <AlertDescription>{done}</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <CircleAlert aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {items === null && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {items?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {owner ? "Nothing is waiting for your approval." : "You have no approval requests."}
        </p>
      )}
      {items?.map((item) => (
        <ApprovalCard
          key={item.po_number}
          item={item}
          owner={owner}
          onDone={(text) => {
            setDone(text);
            load();
          }}
        />
      ))}
    </div>
  );
}
