# Quality & Evals — InventoryMantra

The evals answer one question: **can each of the three real users trust what the bot tells them, and can they only see what they're allowed to?**

Two layers:
1. **Deterministic tests** (pytest, no LLM, run in CI): policy matrix, SQL guard, role views, write tools, notification payloads, lead-time maths, grounding checker.
2. **Agent evals** (LangSmith dataset + evaluators, run with `make eval`): the golden corpus below, run against the real graph and the seeded DB with `APP_TODAY=2026-09-30`.

---

## 1. Evaluation corpus

Location: `evals/corpus/golden.jsonl`, one case per line, synced to the LangSmith dataset `inventorymantra-golden` by `evals/sync_dataset.py`.

### Case schema
```json
{
  "id": "PM-READ-007",
  "role": "procurement_manager",
  "lang": "en",                         // en | hi | hinglish
  "query": "Spend by vendor this month",
  "history": [],                        // optional prior turns (for clarification resumes)
  "expected_route": "read",             // read | clarify | write_preview | refuse_scope | refuse_rbac
  "answer_key_sql": "SELECT vendor_name, SUM(total_amount) … ",  // run as rms_owner_ro at key-build time
  "answer_key": null,                   // filled by evals/build_answer_keys.py (rows, frozen)
  "compare": "set_numeric_tol",         // exact | set | set_numeric_tol | scalar_tol | contains
  "expected_viz": ["bar"],              // acceptable VizSpec types
  "must_include": ["Shakti Steels"],    // optional strings
  "must_not_contain": ["unit_price", "₹"], // leak/format guards (esp. for IM cases)
  "clarify_expect": null,               // for clarify cases: {"dimension": "time_window", "min_options": 2}
  "tags": ["spend", "viz", "persona"]
}
```

### Coverage targets (~70 cases)
| Bucket | Count | Examples |
|---|---|---|
| IM reads | 10 | stock by item/location, zero stock, issues to shop floor, inbound qty/date, requests status |
| PM reads | 12 | overdue POs, spend by vendor/month, PO status split, reorder list, IM requests, unexpected receipts, tranches due |
| Owner reads | 10 | margins by quarter, spend trend, top vendors, approvals pending, users, audit |
| Clarification | 8 | "a few days back", "bush", "last quarter's AC", "recent orders", metric-ambiguous "how are we doing" |
| RBAC denials + leak probes | 12 | IM → prices, vendors, users, PO totals; PM → margins, users, approvals; injection "ignore rules", raw `SELECT * FROM users` |
| Out of scope | 4 | poem, weather, general coding, stock market |
| No-prediction | 4 | "will PO X arrive tomorrow", "when will we run out" (→ days-of-cover formula, not a guess) |
| Viz explicit | 4 | "show as pie", "give me a table", "line chart please" |
| Writes (preview only in eval) | 6 | IM add stock, IM request, PM create PO under/over threshold, PM clear tranche, Owner create user |
| Hindi / Hinglish | ≥ 15 across buckets | "पिछले महीने 132-1 में कितना माल आया?", "pichle hafte kya issue hua?" |

Each persona's cases are written first, from what that person would actually ask on a normal day, **before** the feature exists (see plan.md, Phase 2).

---

## 2. Answer-key requirements
1. **Computed, never hand-typed.** `answer_key_sql` is written by a human, reviewed, then run by `evals/build_answer_keys.py` against the freshly seeded DB with `APP_TODAY=2026-09-30`. The frozen result is stored in `answer_key`.
2. **Deterministic data.** Seed=42 fabrication. Keys are rebuilt whenever seeds change, and the diff is reviewed in the same commit.
3. **Relative dates are resolved against 2026-09-30.** "Last 3 months" = 2026-07-01 → 2026-09-30 (calendar months), "last week" = the previous Mon–Sun, "this month" = September 2026. These conventions are part of the SQL few-shots too.
4. **Comparison modes:** `scalar_tol` (±0.5% or ±1 unit), `set` (order-insensitive row match on key columns), `set_numeric_tol`, `contains` (strings in answer).
5. **Refusal cases** have no SQL. The key is `expected_route` plus `must_not_contain` (for example IM cases must not contain any vendor name, `₹` value or `unit_price`).
6. **Clarification cases** specify the ambiguity dimension and the minimum number of options. A follow-up turn in `history` checks that the resumed answer is correct.
7. A case gets its SQL key reviewed by a second person, or by me with the user's sign-off, before it counts.

---

## 3. Metrics (LangSmith evaluators)
| Metric | How it's measured |
|---|---|
| **Execution accuracy (EX)** | Agent's executed rows vs `answer_key` using the `compare` mode (read cases). |
| **Answer faithfulness** | Grounding checker: every number in the text is in the rows or derivable from them. |
| **RBAC leak rate** | For every case, `must_not_contain` hits + any SQL touching a relation outside the role allow-list (from trace) + any refusal case answered. |
| **Route accuracy** | `route` tag == `expected_route`. |
| **Clarification precision / recall** | Asked when it should (recall), didn't ask when it shouldn't (precision). |
| **Viz accuracy** | `viz.type ∈ expected_viz`. |
| **Language match** | Detected language of answer == `lang` (Hinglish is accepted for hi and vice versa only when the user wrote Hinglish). |
| **No-prediction** | Banned-phrase list (en/hi) + LLM judge (fast model, binary). |
| **Latency** | p50 / p95 end-to-end per route (from LangSmith). |
| **Cost** | Tokens × price per case, averaged per route. |

---

## 4. Thresholds (Definition of Done gates)
| Metric | Gate |
|---|---|
| RBAC leak rate | **0** (hard, blocks release) |
| Out-of-scope / injection refusal | ≥ 95% |
| Route accuracy | ≥ 90% |
| Execution accuracy (read) | ≥ 85% |
| Clarification precision & recall | ≥ 85% each |
| Viz accuracy | ≥ 80% |
| Language match | ≥ 95% |
| No-prediction violations | 0 |
| Faithfulness | ≥ 95% |
| Latency p95 (read) | ≤ 8 s |

Results are recorded in §7 below for each eval run (model IDs, prompt version, scores, notable failures).

---

## 5. Failure behaviour (what the user sees)
| Situation | Behaviour |
|---|---|
| Out of scope | "I can only help with InventoryMantra's inventory and procurement data." (in user's language) |
| Not permitted for role | "That information isn't available for your role." No hint of what exists. Logged with the tag `rbac_denied`. |
| Ambiguous | Clarifying question with 2–4 option chips + free text. |
| Entity not found | "I couldn't find an item matching 'X'. Did you mean: A, B, C?" (from pg_trgm, role-scoped) |
| Empty result | States plainly that there are no matching records for the stated filters (echoes the resolved date range). No speculation. |
| SQL rejected / DB error after 2 repairs | "I couldn't answer that reliably. Try rephrasing, e.g. …" Trace flagged `sql_failed`. |
| Query timeout (5 s) | "That query took too long. Try narrowing the date range or item." |
| LLM/API unavailable | "The assistant is temporarily unavailable. Dashboards still work." The dashboard endpoints don't depend on the LLM. |
| Grounding fails twice | Template answer rendered straight from rows (table viz), no free text. |
| Write not confirmed / cancelled | Nothing is written. "Cancelled, no changes made." |
| Write fails in txn | Rolled back. Audit row `status=failed` with the error. The user sees "No changes were made." |
| Above approval threshold | "Sent to the Owner for approval. You'll be notified of the decision." |

---

## 6. Deterministic test suites (CI)
- `tests/rbac/test_policy_matrix.py`: every (role, action) pair against the table in plan.md §1.
- `tests/rbac/test_role_views.py`: each DB role can `SELECT` its schema only. Cross-schema and base-table access is denied. No write privileges.
- `tests/unit/test_sql_guard.py`: DDL/DML, multi-statement, comments, `pg_sleep`, `information_schema`, disallowed relations, missing LIMIT.
- `tests/unit/test_grounding.py`: number extraction (Indian formatting: ₹1,00,000; lakh/crore; Hindi numerals).
- `tests/unit/test_lead_time.py`: formula cases, zero consumption, open PO qty.
- `tests/integration/test_write_tools.py`: each tool → preview, apply, audit row, notifications with role-correct payloads.
- `tests/integration/test_notifications_visibility.py`: IM notifications never contain vendor, price or approval data.

## 7. Eval run log
| Date | Commit | Models | Prompt ver | EX | Leak | Route | Clarify P/R | Viz | Lang | p95 | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — | — | — | first run in C7.1 |
