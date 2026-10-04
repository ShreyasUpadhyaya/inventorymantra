// Owner approvals API (backend/app/api/routes/approvals.py).

import { apiJson } from "./api";

export type ApprovalLine = {
  item_name: string;
  quantity: number;
  unit: string;
  unit_price: number;
  line_total: number;
};

export type ApprovalMessage = {
  kind: "question" | "reply";
  author_name: string;
  body: string;
  created_at: string;
};

export type PendingApproval = {
  po_number: string;
  status: "pending" | "approved" | "rejected";
  vendor_name: string | null;
  total_amount: number;
  requested_by_name: string;
  requested_at: string;
  expected_delivery_date: string | null;
  lines: ApprovalLine[];
  messages: ApprovalMessage[];
};

const post = (path: string, body: object) =>
  apiJson<{ message: string }>(path, { method: "POST", body: JSON.stringify(body) });

export const approvals = {
  pending: () => apiJson<PendingApproval[]>("/approvals"),
  mine: () => apiJson<PendingApproval[]>("/approvals/mine"),
  ask: (poNumber: string, message: string) =>
    post(`/approvals/${encodeURIComponent(poNumber)}/ask`, { message }),
  reply: (poNumber: string, message: string) =>
    post(`/approvals/${encodeURIComponent(poNumber)}/reply`, { message }),
  decide: (poNumber: string, decision: "approve" | "reject", note?: string) =>
    apiJson<{ message: string }>(`/approvals/${encodeURIComponent(poNumber)}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision, note: note?.trim() || null }),
    }),
};
