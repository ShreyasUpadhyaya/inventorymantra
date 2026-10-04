// Owner approvals API (backend/app/api/routes/approvals.py).

import { apiJson } from "./api";

export type ApprovalLine = {
  item_name: string;
  quantity: number;
  unit: string;
  unit_price: number;
  line_total: number;
};

export type PendingApproval = {
  po_number: string;
  vendor_name: string | null;
  total_amount: number;
  requested_by_name: string;
  requested_at: string;
  expected_delivery_date: string | null;
  lines: ApprovalLine[];
};

export const approvals = {
  pending: () => apiJson<PendingApproval[]>("/approvals"),
  decide: (poNumber: string, decision: "approve" | "reject", note?: string) =>
    apiJson<{ message: string }>(`/approvals/${encodeURIComponent(poNumber)}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision, note: note?.trim() || null }),
    }),
};
