# Decision log (ADR-lite)

Format: **Context → Decision → Consequences**. Status is one of Accepted, Proposed or Superseded. Add new entries at the bottom and never rewrite an accepted one; supersede it instead.

---

### D-001 · Product name: **InventoryMantra**
- **Context:** The repo name ("Railway-mangement-system") describes the client, not the product. The first choice, "StockSaarthi", was rejected by the user because "stock" reads like the stock market.
- **Decision:** Call it **InventoryMantra**. It names the domain (inventory) and carries an Indian, bilingual flavour ("mantra" = guiding formula). Other names we considered: StockSaarthi (rejected), Bhandar AI, RailKosh.
- **Consequences:** Used in the UI, README and LangSmith project names. The repo name stays as-is.
- **Status:** Accepted

### D-002 · Local Docker Postgres, not the live Railway DB
- **Context:** The export came from a live Railway.app Postgres. Chat writes and eval answer keys need stable, disposable data.
- **Decision:** Run Postgres 16 in Docker for dev and evals. Seed it from sanitised CSVs in `data/seed/` plus deterministic fabricated data. The live DB is never touched by the app. The deployed demo uses its own managed Postgres, seeded the same way.
- **Consequences:** Reproducible demo and evals, and writes are safe. The data is a snapshot as of 2026-09-22.
- **Status:** Accepted

### D-003 · OpenAI as the LLM provider, with two model tiers
- **Context:** The user chose OpenAI. Different nodes need different cost and quality.
- **Decision:** Use `langchain-openai` `ChatOpenAI` with **`OPENAI_MODEL_REASONING`** (SQL generation, answer composition) and **`OPENAI_MODEL_FAST`** (guard, clarification, viz routing). Exact model IDs get pinned here in C4.1 after checking what the account has. Temperature 0 and structured output (JSON schema) wherever a node returns data.
- **Consequences:** The model is swappable via env. Eval results are recorded per model ID.
- **Status:** Accepted (model IDs pending C4.1)

### D-004 · RBAC in two layers: role views + read-only DB roles
- **Context:** LLM-generated SQL can't be trusted to respect visibility rules.
- **Decision:** (1) Each role gets a schema (`v_im`, `v_pm`, `v_owner`) of views exposing only permitted columns. For example, `v_im.inbound_by_item` has item, pending qty and expected date, but no vendor or price. (2) Three Postgres login roles (`rms_im_ro`, `rms_pm_ro`, `rms_owner_ro`) with `SELECT` only on their schema. The agent's SQL runs as the caller's role user.
- **Consequences:** A prompt-injected query can't read beyond the role even if it gets past the validator. Adding a new question type sometimes means adding a view column.
- **Status:** Accepted

### D-005 · SQL guard with sqlglot
- **Decision:** Parse every generated statement with sqlglot (Postgres dialect). Reject it unless it is a single `SELECT`/`WITH` statement that references only allow-listed relations, has no functions from a deny-list (`pg_sleep`, `dblink`, `lo_*`, …) and no comments or semicolon chains. Inject `LIMIT 500` if it's missing. Execute with `SET TRANSACTION READ ONLY` and `statement_timeout = 5s`.
- **Consequences:** Deterministic and unit-testable. A rejected query triggers one repair attempt, then a safe failure message.
- **Status:** Accepted

### D-006 · Writes only through typed tools, never LLM SQL
- **Decision:** Write actions are LangChain tools with Pydantic args: `record_stock_movement`, `raise_stock_request`, `create_purchase_order`, `update_po_status`, `clear_payment_tranche`, `set_reorder_policy`, `decide_approval`, `create_user`. Each tool checks `policy.can(role, action)`, builds a preview, and pauses the graph with a LangGraph `interrupt()` for the user's confirmation.
- **Consequences:** Writes are auditable, reversible by design (before/after state goes to `agent_audit_log`) and testable without an LLM.
- **Status:** Accepted

### D-007 · Approval rule: confirm always; Owner approval for POs above ₹1,00,000
- **Context:** The user chose "Confirm + owner approval".
- **Decision:** Every write needs user confirmation. A PM PO with `total_amount > PO_APPROVAL_THRESHOLD_INR` (default 100000) is created as `pending_approval` and shows in the Owner's queue. Owner decisions notify the PM with the result only. The Owner's own writes are auto-approved.
- **Status:** Accepted

### D-008 · Visual choice: backend returns a `VizSpec`; frontend renders it
- **Decision:** The answer contains a Pydantic `VizSpec {type: none|kpi|table|bar|line|pie|stacked_bar|flow, title, x, y[], series?, data_ref, mermaid?}`. The choice is **rules first, LLM second**. Rules: 1 row × 1 number → kpi; time column + measure → line; category + measure with ≤ 12 categories → bar; share of total with ≤ 6 parts → pie; process/lifecycle question → flow; many columns → table. The fast model only breaks ties or honours an explicit request. The frontend uses Recharts (charts), a shadcn table and Mermaid (flow).
- **Consequences:** Predictable, testable viz accuracy and no chart-code generation by the LLM.
- **Status:** Accepted

### D-009 · Clarification via LangGraph interrupt
- **Decision:** The `clarify` node decides (structured output) whether a question is ambiguous about the **time window**, the **entity** (entity resolver returns > 1 plausible match, or 0), or the **metric**. If it is, it calls `interrupt({question, options[]})`. The UI shows option chips plus free text, and the resume carries the choice. When a default is conventional, it is used and stated instead of asking.
- **Status:** Accepted

### D-010 · Grounding and the no-prediction rule
- **Decision:** After composing, a deterministic check extracts numbers from the answer and verifies each one exists in the result rows, or is derivable (sum/avg/count/percent, rounded). A banned-phrase list (en + hi: "will probably", "expected to reach", "should arrive", "शायद", "उम्मीद है कि" …) combined with a fast-model judge flags predictions. On failure the answer is regenerated once, then falls back to a template answer from the rows.
- **Status:** Accepted

### D-011 · Lead-time and reorder logic is deterministic
- **Decision:** `avg_daily_use = outbound qty over last 90 days / 90`; `days_of_cover = on_hand / avg_daily_use`; `reorder_point = avg_daily_use × lead_time_days + safety_stock`; `order_by_date = today + days_of_cover − lead_time_days`. Items where `on_hand + open PO qty ≤ reorder_point` are "reorder now". A daily scan (plus one on dashboard load) creates PM notifications, deduplicated per item per day.
- **Consequences:** These are shown as calculations with their inputs, not predictions, which matches the "facts only" rule.
- **Status:** Accepted

### D-012 · Fabricated data (deterministic, labelled)
- **Decision:** Script `backend/scripts/fabricate.py` (seed=42) generates: `reorder_policy` for stocked active items (lead time 7–45 days by sub-category, min level and reorder qty from consumption), `sales_orders`/`sales_order_lines` for Finished Goods Oct 2025 → Sep 2026 (selling price = cost × 1.18–1.45), a few open `stock_requests`, and demo users `owner@demo`, `procurement@demo`, `inventory@demo`. Rows carry `source='demo'` where a column exists.
- **Status:** Accepted

### D-013 · Frozen "today" for evals
- **Decision:** `app_today()` returns `APP_TODAY` if it is set, otherwise the current date in Asia/Kolkata. Evals and answer keys use `2026-09-30`.
- **Status:** Accepted

### D-014 · Auth: bcrypt + JWT (access) in an httpOnly cookie, with refresh rotation
- **Decision:** Short-lived access JWT (15 min) plus a refresh token (7 days, hashed in `refresh_tokens`, rotated on use). Cookies are `HttpOnly; Secure; SameSite=Lax`. `token_version` on the user allows forced logout. The role comes from the DB on every request, not only from the token.
- **Status:** Accepted

### D-015 · Streaming: SSE from FastAPI
- **Decision:** `/chat` streams SSE events: `status` (node progress), `clarify`, `confirm`, `answer` (text + VizSpec + rows), `error`. It's simpler than WebSockets and fits the request/response chat.
- **Status:** Accepted

### D-016 · Schema context in the prompt, no vector store
- **Decision:** The role's views, compact column docs and ~15 few-shot NL→SQL pairs fit comfortably in context. Entity names are resolved with `pg_trgm` similarity, not embeddings.
- **Consequences:** Fewer moving parts. If the schema grows, revisit.
- **Status:** Accepted

### D-017 · Notifications: DB table + polling
- **Decision:** A `notifications` table (recipient_user_id or recipient_role, type, title, body, payload JSON built per role, read_at). The frontend polls every 20 s. Producers are write tools, approvals and the lead-time scan.
- **Status:** Accepted

### D-018 · Voice (post-scope): free-tier / local STT behind an interface
- **Context:** The user wants to keep API cost low.
- **Decision:** A `SpeechToText` interface. The primary is Deepgram (free credit, hi/en). The zero-cost fallbacks are self-hosted `faster-whisper` or the browser Web Speech API. ("Wispr Flow" is a dictation app with no public API.) The transcript is shown and editable before sending.
- **Status:** Proposed (after DoD)

### D-019 · Deployment
- **Decision:** Frontend on Vercel, backend + Postgres on Railway (or Render). Secrets live only in each platform's env settings. CORS is limited to the frontend origin.
- **Status:** Proposed (C7.3)

### D-020 · Answers to the prompt open questions (user, 2026-09-30)
- Hindi replies always use the formal **"aap"**.
- A denial is **one fixed sentence** with no pointer to who can see the data, so an injection probe learns nothing.
- **Mixed requests:** answer the permitted part, and use the fixed denial sentence for the rest.
- The **PM can see their own audit trail** (`v_pm.my_audit_log`); only the Owner sees everyone's.
- **Assumed defaults are stated only when they shaped the answer.** If the user didn't give a period, item or measure and the bot filled one in, it says so in one short clause. Otherwise it says nothing about defaults.
- **Help mode** exists, with at least 3 example questions per role (used in the demo).
- **Status:** Accepted
