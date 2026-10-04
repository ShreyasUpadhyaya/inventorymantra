# Write path — how InventoryMantra changes the database

Status: **APPROVED by the user on 2026-10-04** (gate G2 passed; built in Phase 6, commits C6.1–C6.4).
This document describes how chatbot writes are built: which tables each action touches, in what order, and what guards them. It follows the conventions already in the data. Every stock change is an `inv_transactions` row with a **signed** quantity, plus an update of the matching `inv_current_stock` row (unique per item × location). PO receipts and unplanned receipts link to their stock transaction through `inv_transaction_id`.

---

## 1. Principles
1. **The LLM only extracts intent and arguments.** It never writes SQL for a write, never does arithmetic that gets stored, and never picks IDs. The server resolves names to IDs, computes totals, validates and writes.
2. **One service function per action** (`backend/app/services/*.py`). The LangChain tool is a thin adapter over it. Dashboard buttons (for example Owner approve/reject) call the **same** service, so there's one code path.
3. **Preview → confirm → apply.** Nothing is written before the user confirms the exact preview.
4. **All-or-nothing.** Each confirmed action runs in one DB transaction. On any error the whole thing rolls back.
5. **Append-only history.** Stock history is never edited or deleted. A mistake is corrected with a reversing entry. No hard deletes are exposed through chat at all.
6. **Every attempt is audited**, whether it succeeded, failed, was cancelled or is pending approval, in `agent_audit_log` with before/after state.

## 2. Lifecycle of one write

```mermaid
sequenceDiagram
  participant U as User
  participant G as LangGraph
  participant T as Tool adapter
  participant S as Service
  participant D as Postgres
  U->>G: "Add 3 AC Contactor to 132-2"
  G->>G: guard → intent=write
  G->>T: LLM picks tool + raw args (structured output)
  T->>S: plan(args, user)
  S->>D: resolve item/location (pg_trgm), read current state
  S->>S: validate + policy.can(role, action) + compute numbers
  S-->>G: Preview {summary, fields, effects, preview_hash, state_version, expires_at}
  G-->>U: interrupt → confirm card
  U->>G: Confirm (resume with preview_hash)
  G->>S: apply(preview, operation_id)
  S->>D: BEGIN
  S->>D: INSERT audit (status=pending, before_state)  [idempotency: UNIQUE operation_id]
  S->>D: SELECT … FOR UPDATE on affected rows
  S->>S: re-check state_version; if changed → abort, re-preview
  S->>D: mutations (see §4)
  S->>D: INSERT notifications (role-scoped payloads)
  S->>D: UPDATE audit (status=success, after_state)
  S->>D: COMMIT
  S-->>G: Result
  G-->>U: "Done: 3 pcs added. 132-2 now has 15."
```

On an exception: `ROLLBACK`, then a separate short transaction writes the audit row with `status=failed` and `error`. The user sees "No changes were made", followed by the reason in plain words.

### Guards in detail
| Guard | How |
|---|---|
| **Authorisation** | `policy.can(role, action)` is checked at plan time **and** again at apply time. The role comes from the session, never from the chat text. |
| **Previewed = applied** | `preview_hash` = SHA-256 of the canonical action payload. Apply rejects a resume whose hash doesn't match the stored preview. |
| **Stale preview** | `state_version` = `updated_at` / quantities of the rows that will change. If they changed between preview and confirm, abort and show a fresh preview. Previews also expire after 10 minutes. |
| **Double submit** | `operation_id` = `thread_id` + interrupt ID, with a unique constraint on `agent_audit_log.operation_id`. A second confirm returns the first result. |
| **Concurrency** | `SELECT … FOR UPDATE` on `inv_current_stock` / PO rows, locked in a fixed order (by id) to avoid deadlocks. |
| **Sanity limits** | qty > 0; qty ≤ 100,000 per line; ≤ 20 lines per PO; unit price > 0; item `status='active'`; location `is_active`; no outbound or transfer beyond on-hand stock (no negative stock). |
| **Ambiguity** | If an item, vendor or location matches several records, ask back (same clarify interrupt as reads) before any preview. |
| **Privacy in preview** | Previews are built from the caller's role view, so an IM receiving against a PO sees item and qty, never price or vendor. |
| **Secrets in traces** | `create_user` generates the temp password **server-side** after confirmation. It never enters LLM context or LangSmith, and is shown once in the UI only. |

## 3. Status models
- **Purchase order:** `pending_approval → placed → partial → received`, plus `rejected` and `cancelled`. The existing data uses `placed`, `partial` and `received`; we add the other three.
- **Stock request:** `open → ordered (linked PO) → fulfilled`, plus `rejected`.
- **Approval:** `pending → approved | rejected`.
- **Audit row:** `pending → success | failed`, plus `cancelled` (user declined) and `awaiting_approval`.

## 4. Action catalogue
| Tool (service) | Roles | Tables written (in order) | Notifications |
|---|---|---|---|
| `record_stock_movement` (kind = inbound / outbound / issue) | IM, PM, Owner | `inv_transactions` (signed qty, `reference_type` = `manual_adjustment` or `warehouse_issue`, `issued_to_id` for issues; **no unit_cost** from the IM) → upsert `inv_current_stock` (qty, `last_transaction_at`, `updated_at`) | PM, if the new qty ≤ reorder point: "Low stock: X at 132-2" |
| `transfer_stock` | IM, PM, Owner | 2 × `inv_transactions` (`transfer_out` −q at source, `transfer_in` +q at destination, `reference_type='transfer'`, `transfer_location_id`) → both stock rows | — |
| `receive_against_po` | IM, PM, Owner | `proc_po_receipts` → `inv_transactions` (inbound, `reference_type='purchase_order'`, `unit_cost` from the PO line, which the server sets and the IM never sees) → stock row → `proc_po_lines.quantity_received` → PO status recomputed (`partial`/`received`) | PM: "PO-2026-0003: 50/100 received" |
| `record_unexpected_receipt` | IM, PM, Owner | `proc_unexpected_receipt_headers` → `proc_unexpected_receipts` → `inv_transactions` (`reference_type='unexpected_receipt'`) → stock row | PM: "Unplanned delivery of X from <vendor text>" |
| `raise_stock_request` | IM, PM, Owner | `stock_requests` (status `open`) | PM role: "IM requests 200 Hex Nut M3 by 10 Oct" |
| `create_purchase_order` | PM, Owner | Next number from sequence `po_number_seq` (initialised above PO-2026-0057) → `proc_purchase_orders` (server-computed subtotal/tax/total; status `placed`, or `pending_approval` if a PM order is > ₹1,00,000) → `proc_po_lines` → `proc_po_payment_tranches` (from vendor terms if recognised, else one tranche "as per terms") → `approvals` if needed → link `stock_requests.po_id` + status `ordered` | Owner (if approval needed); IM (if it fulfils their request, **item, qty and expected date only**) |
| `update_po_expected_date` / `cancel_po` | PM, Owner | PO (+ lines) dates/status; cancel only if nothing has been received | IM, if a linked request's date changes (date only) |
| `clear_payment_tranche` | PM, Owner | `proc_po_payment_tranches.cleared_at/by` | — |
| `set_reorder_policy` | PM, Owner | upsert `reorder_policy` | — |
| `decide_approval` | Owner | `approvals` → PO status `placed` or `rejected` | PM: result + optional note; IM: date only, if linked to their request |
| `ask_about_approval` | Owner | `approval_messages` (question); the PO keeps waiting (C6.6) | PM who asked: the question |
| `reply_to_approval` | PM (own requests only) | `approval_messages` (reply) | Owner: the answer |
| `create_user` / `deactivate_user` | Owner | `users` (bcrypt, `created_by`), deactivation bumps `token_version` (forces logout) | — |
| `reset_password` | Owner | new temporary password, `must_change_password`, all sessions ended (C5.1b) | — |
| `create_vendor` | PM, Owner | `proc_vendors` | — |

**Not available via chat, on purpose:** deleting anything, editing past transactions, changing prices on received POs, changing roles of existing users. These are high-risk and rarely needed, and they're easy to add later behind the Owner role.

## 5. Multi-action messages
"Add 3 AC Contactor to 132-2 and 5 to 132-1" becomes **one** preview listing both effects, one confirmation and **one** transaction. Mixed read and write ("add 3 and tell me the total") runs the write first after confirmation, then the read.

## 6. Tests (written before the tools, Phase 2 / 6)
- For every tool: a happy-path preview; apply writes exactly the listed rows; audit row success; correct notifications with **role-filtered payloads**.
- Negative: wrong role (denied at plan and apply), negative stock, inactive item/location, qty limits, expired preview, hash mismatch, stale state, double confirm (idempotent), DB error mid-transaction (full rollback + failed audit row).
- Approval: a PM order over the threshold becomes `pending_approval`; the Owner approves it and it becomes `placed` with the PM notified; the Owner rejects it and it becomes `rejected`.
- Concurrency: two parallel outbounds on the same stock row can't take it below zero.
