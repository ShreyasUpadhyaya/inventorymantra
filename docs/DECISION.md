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

### D-021 · Current stock is the source of truth for on-hand quantity
- **Context:** In the export, `inv_current_stock` does not equal the sum of `inv_transactions` for 310 of 561 item-location pairs. 110 stock rows have no transactions at all, because opening balances were never recorded as movements.
- **Decision:** On-hand answers come from `inv_current_stock`. `inv_transactions` is movement history (issues, receipts, transfers), not a ledger from zero. New writes keep the invariant *stock change = transaction quantity* for every movement they record (tested in Phase 6). Consumption for lead-time maths uses outbound transactions only.
- **Consequences:** "How much do we have" and "how much moved" can legitimately differ. When a user asks the bot to reconcile them, it says opening balances aren't recorded as transactions.
- **Status:** Accepted

### D-022 · Missing master data is stated, not hidden (e.g. 40 uncategorised items)
- **Context:** 40 items have no category/sub-category. The user wants these shown as "Uncategorised", with answers making clear that the data doesn't exist.
- **Decision:** Role views expose `category_name = 'Uncategorised'` plus a boolean `category_recorded`. When an answer groups or filters by category and uncategorised items are involved, the answer says so explicitly, e.g. "40 items have no category recorded in the database, so they're shown as Uncategorised." This follows the existing "facts only / say it isn't recorded" rule (PROMPTS §1 rule 2), so no prompt change is needed. It gets an eval case in C2.1.
- **Status:** Accepted

### D-023 · Local DB host is 127.0.0.1:5433
- **Context:** Port 5432 is taken by a local Postgres on the dev machine. On Windows, `localhost` tries IPv6 first and the connection hangs.
- **Decision:** Docker Postgres binds `127.0.0.1:5433`, and `DATABASE_URL` uses `127.0.0.1`. Connections set `connect_timeout`.
- **Status:** Accepted

### D-024 · Demo data specifics (refines D-012; ranges approved by the user 2026-09-30)
- **Reorder policies only for regularly used items:** items issued in at least 2 distinct weeks during the 90 days to 2026-09-30 (34 items). One-off project items get no policy; the PM can add one later. This gives about 15 "reorder now" alerts instead of more than 80 noisy ones.
  - `min_level` is about 30 days of average use and `reorder_qty` about 60 days, rounded to sensible pack sizes. `safety_stock` is 7 days of use.
  - Lead time is drawn from a range per sub-category: consumables 7–14 days, mechanical 10–25, metal and non-metal raw material 15–30, sub-assemblies 20–40, electrical 21–45, anything else 14–30.
  - The preferred vendor is the one on the item's most recent PO, if there is one.
- **Sales:** Finished Goods only, Oct 2025 to Sep 2026, sold to 6 fictional customers.
  - Unit cost is ₹20,000–2,50,000 and quantities are 1–5, so quarterly revenue (about ₹1–2.3 crore) sits plausibly next to procurement spend.
  - Each quarter has its own markup band inside the approved 1.18–1.45 range, so the margin trend isn't flat. Quarterly margins come out at 23.6%, 25.2%, 20.9% and 24.7%.
- **Stock requests:** 4 open requests from the IM for the items with the least cover, plus 1 fulfilled request.
- **Users:** `owner@`, `procurement@` and `inventory@demo.inventorymantra`. The password comes from `DEMO_PASSWORD`, or is generated and printed once by `make seed`.
- **Not seeded:** notifications and approvals. Those are produced by the real flows in Phase 6.
- All generated IDs are uuid5 values (deterministic), and the data is generated as of 2026-09-30, independent of `APP_TODAY`.
- **Status:** Accepted

### D-025 · Role view schemas: implementation details (refines D-004)
- **Schemas:** `v_im` has 8 views, `v_pm` 19 and `v_owner` 22 (migration 0003). The views run with the migrator's rights, so the role users need no rights on base tables. `REVOKE ALL ON SCHEMA public FROM PUBLIC` makes base tables unreachable for them.
- **Role users:** `rms_im_ro`, `rms_pm_ro` and `rms_owner_ro`. They are not superusers, can't create databases or roles, and don't inherit rights. Each has its own schema as its search path (so the LLM can write `FROM items`), read-only transactions by default, a 5 s statement timeout, a 30 s idle-in-transaction timeout and a limit of 20 connections. They are created NOLOGIN in the migration, and `make seed` enables login from `READONLY_DB_PASSWORD`, so no secret lives in a migration.
- **Per-request context:** the API sets `app.user_id` and `app.today` inside each read-only transaction (`app/db/readonly.py`). The `my_*` views are `security_barrier` views filtered on `app.user_id`, and with no user they return no rows. Date maths (overdue days, reorder dates) uses `app.today`, so evals are deterministic.
- **Column rules, enforced by tests:**
  - The IM's schema has no column matching price, cost, amount, total, spend, vendor, payment, tranche, margin, revenue, profit, email, password, hash, token or customer.
  - The PM's schema has no margin, revenue, profit, customer, email, password, hash or token columns, and none of the Owner-only views.
  - The IM can see PO **numbers** and pending quantities or dates (needed to receive goods), but never vendor or price.
- **Known limitation:** any Postgres user can read the system catalogs (`pg_catalog`, `information_schema`), which reveals object *names*. Row data stays protected. The SQL guard (D-005, C4.2) rejects catalog references, and denials never echo names.
- **Status:** Accepted

### D-026 · Entity matching for messy user input (refines D-016; raised by the user)
- **Context:** Users won't type `MS Bush – OD40×ID26.5×70mm`. They type `ms bush 40`, `OD40xID26x70`, `torx screw`, `AC contacter`. Plain `pg_trgm` similarity finds the right family but is weak on what matters most in this catalogue, sizes and specs. "hex nut m3" ties M3 and M8 at 0.73, and "ms bush 40" ties OD40 and OD42.
- **Decision:** a deterministic matcher in code (built in C4.2 and unit-tested offline in C2.2), applied to each **entity mention** (item, vendor, location, PO). The guard extracts the mentions, so the matcher never sees the whole sentence.
  1. **Exact item code** (`BO-EL-0144`) or **PO number** (normalised: `PO 3`, `po-0003` and `3` all become `PO-2026-0003`) means a direct hit.
  2. **Normalise** both sides: lowercase, `×` and `*` become `x`, dashes become spaces, collapse spaces. Then expand **synonyms taken from the catalogue's own naming standards** (`inv_sub_categories_2.convention_notes`): Torx means Hexalobular, Pen Head means Pan Head, ISMB means MS Beam.
  3. **Candidates:** the top 15 by `greatest(similarity, word_similarity)` with a score of at least 0.3, among active items.
  4. **Spec tokens decide:** words containing a digit are split into atoms (`od40xid26x70` gives od 40, id 26 and 70; units are dropped). Every atom must be present in the candidate, where a bare number matches any prefix and `id26` matches `id26.5`. A conflicting size (`m3` vs `m8`) eliminates the candidate.
  5. **Word preference:** if some candidates contain every plain word the user typed, only those are kept. This removes noise like "Push Button Box" for "bush".
  6. **Outcome:**
     - **match** if the top score is at least 0.5 and it's unique or at least 0.15 ahead of the next. The answer **names the matched item in full**, so a wrong match is visible at once.
     - **clarify** if two or more candidates score at least 0.5, offering up to 4 as options.
     - **not_found** otherwise, showing up to 3 suggestions scoring at least 0.35, or none.
  7. All matching runs inside the caller's role scope. An IM matching a vendor name gets the fixed denial, not "not found".
- **Prototype check (seeded data):** `OD40xID26x70`, `ms bush 40`, `hex nut m3`, `torx screw m3`, `AC contacter`, `boom barrier motor` and `ss sheet 304 1500x3000x2` all match. `ms bush`, `hex nut`, `ss sheet` and `gear box` all clarify. `torx screw m4` gives not_found, suggesting the M3 Hexalobular screw, and `titanium flange` gives not_found.
- **Consequences:** a new route, `not_found`, in the eval schema, and 16 `ER-*` corpus cases. The guard gets an `entity_mentions` output field (PROMPTS.md v0.3, approved 2026-10-01).
- **Status:** Accepted (prompt field approved as v0.3 on 2026-10-01)

### D-027 · Auth implementation details (refines D-014; built in C3.1)
- **Tokens:**
  - The access JWT is HS256 and lasts 15 min. Its claims are `sub`, `ver` (token_version), `iss`, `aud`, `iat` and `exp`, all required.
  - **The role is not in the token.** `current_user` loads the user on every request, so the role, `is_active` and `token_version` all come from the DB. Demotion, deactivation and "log out everywhere" take effect on the next request.
  - The refresh token is 48 random bytes. Only its SHA-256 hash is stored, it lasts 7 days, and it's **rotated on every use**. If an already-rotated token is presented, that counts as theft: every refresh token of that user is revoked and `token_version` is bumped, which also kills live access tokens.
- **Cookies:** `im_access` (path `/`) and `im_refresh` (path `/auth` only) are both HttpOnly and SameSite=Lax, with `Secure` set from `COOKIE_SECURE` (true in deployment). A Bearer header is also accepted, for API clients and tests.
- **CSRF:** SameSite=Lax cookies plus JSON-only request bodies, so a cross-site HTML form can't post JSON and a form post gets a 422. CORS is limited to the frontend origin, with methods GET and POST and headers Content-Type and Authorization.
- **Login hardening:**
  - One identical 401 message for an unknown email, a wrong password or an inactive user. Unknown emails are checked against a dummy bcrypt hash, so the response takes the same time.
  - Email is trimmed and lowercased.
  - 5 failures per email and IP within 15 min gives a 429, and while blocked even the correct password is refused. The counter is in memory per process, which is fine for one instance; use Redis or the DB when scaling out.
- **Config:** `JWT_SECRET_KEY` must be at least 32 characters, otherwise token creation and checks refuse to run. CI uses its own throwaway value.
- **Status:** Accepted

### D-028 · Owner user management (C3.2)
- **Endpoints:** `GET /users`, `POST /users`, `POST /users/{id}/deactivate` and `/activate`. They are guarded by `require_action("create_user" | "deactivate_user")` from `app/policy/permissions.py`, the single source of truth for authorisation (default deny).
- **Passwords are never chosen through the API.** `POST /users` generates a temporary password server-side and returns it **once** in the response. It isn't stored (only the bcrypt hash is), logged or audited. A forced change on first login is out of scope for the case study.
- **Deactivation is immediate:** it sets `is_active = false`, revokes every refresh token and bumps `token_version`, so live access tokens die on the next request. The Owner can't deactivate their own account, so they can't lock themselves out.
- **Every change is audited** in `agent_audit_log` (`app/services/audit.py`) with before/after state and no secrets, so it shows in the Owner's audit view. The full write framework (preview, idempotency) arrives in C6.1.
- **Status:** Accepted

### D-029 · Role dashboards (C3.3)
- **`GET /dashboard`** returns `{role, today, kpis[], sections[]}` for the signed-in user's role. Layouts are declared in `app/services/dashboard.py`:
  - Inventory Manager: 4 KPIs and 5 sections.
  - Procurement Manager: 5 KPIs and 7 sections.
  - Owner: 6 KPIs and 9 sections.
- **Same boundary as the chatbot:** every KPI and section query runs inside `readonly_session` as the role's read-only DB user against its view schema. A dashboard bug can't show more than the role may see. Tests also scan the IM payload for vendor, price and people data.
- **Generic sections** have the shape `{viz: table|bar|line|pie, x, y[], columns, rows, empty_message}`, so the frontend can render dashboards and chat answers with the same components (C5.2 and C5.3). Money is returned as numbers; formatting (₹, lakh/crore) is the frontend's job, using each KPI's `format`.
- **Definitions:**
  - "Out of stock" means the total across all warehouses is ≤ 0.
  - "Last completed quarter" means the latest quarter starting before the current quarter (Q2 2026 on 30 Sep 2026).
  - Overdue days and reorder dates use `app_today()`.
- **Status:** Accepted

