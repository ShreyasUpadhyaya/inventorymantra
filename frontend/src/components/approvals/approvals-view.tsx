"use client";

import { CircleAlert, CircleCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { approvals, type PendingApproval } from "@/lib/approvals";
import { formatCell, formatDate } from "@/lib/format";

type Decision = "approve" | "reject";
const message = (err: unknown) =>
  err instanceof ApiError ? err.message : "Something went wrong. Please try again.";

function ApprovalCard({ item, onDone }: { item: PendingApproval; onDone: (text: string) => void }) {
  const [asking, setAsking] = useState<Decision | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: Decision) {
    setBusy(true);
    setError(null);
    try {
      const result = await approvals.decide(item.po_number, decision, note);
      onDone(result.message);
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {item.po_number} · {formatCell(item.total_amount, "total_amount")}
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
        {error && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        {asking ? (
          <div className="grid gap-2">
            <label htmlFor={`note-${item.po_number}`} className="text-sm font-medium">
              {asking === "approve" ? "Approve and place this order?" : "Reject this order?"} Note for{" "}
              {item.requested_by_name} (optional)
            </label>
            <Textarea
              id={`note-${item.po_number}`}
              value={note}
              maxLength={300}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
            />
            <div className="flex gap-2">
              <Button
                variant={asking === "approve" ? "default" : "destructive"}
                disabled={busy}
                onClick={() => decide(asking)}
              >
                {busy ? "Saving…" : asking === "approve" ? "Yes, approve" : "Yes, reject"}
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setAsking(null)}>
                Back
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <Button onClick={() => setAsking("approve")}>Approve</Button>
            <Button variant="outline" onClick={() => setAsking("reject")}>
              Reject
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ApprovalsView() {
  const [items, setItems] = useState<PendingApproval[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = useCallback(() => {
    approvals.pending().then(setItems, (err: unknown) => setError(message(err)));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="grid gap-4">
      <div>
        <h1 className="text-xl font-semibold">Approvals</h1>
        <p className="text-sm text-muted-foreground">
          Purchase orders over the approval limit wait here until you decide.
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
      {items?.length === 0 && <p className="text-sm text-muted-foreground">Nothing is waiting for your approval.</p>}
      {items?.map((item) => (
        <ApprovalCard
          key={item.po_number}
          item={item}
          onDone={(text) => {
            setDone(text);
            load();
          }}
        />
      ))}
    </div>
  );
}
