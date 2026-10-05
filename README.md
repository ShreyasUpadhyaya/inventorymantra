# InventoryMantra

**A bilingual (English / हिन्दी) inventory and procurement assistant for a railway-equipment manufacturer.**

Ask questions in plain language, like "Which POs are overdue?" or "पिछले महीने 132-1 में कितना माल आया?". InventoryMantra turns them into safe SQL, answers only from the data, asks back when a question is vague, and picks the right chart on its own. What you can see and change depends on your role: Inventory Manager, Procurement Manager or Owner.

> Status: **Phases 0–7 complete** (read path, writes, approvals, notifications, dashboards, evals). Every eval gate passes on the 107-case golden set (EX 0.855, faithfulness 1.00, 0 leaks). Voice input (Phase 8) is in. Deployment is out of scope (runs on localhost). See [plan.md](plan.md) and the [technical guide](docs/TECHNICAL_GUIDE.md).

## Features
- 💬 **Chat over the database**: NL → SQL → NL, in English, Hindi or Hinglish
- ❓ **Clarifies vague questions** ("a few days back?" → offers concrete date ranges)
- 📊 **Automatic visuals**: KPI, table, bar, line, pie, stacked or flow diagram, chosen per question
- 🔐 **Role-based access**, enforced by DB role views and read-only DB users, not just prompts
- ✍️ **Writes by chat** (bonus): stock movements, stock requests, purchase orders, with preview → confirm → Owner approval → audit
- 🔔 **Role-scoped notifications** and **lead-time reorder alerts**
- 📈 **Role dashboards**: stock and shortages (IM), pipeline and reorder list (PM), everything plus spend and margins (Owner)
- 🔍 **LangSmith tracing and evals** on every agent run
- 🎙️ **Voice input**: push-to-talk in Hindi or English; the words land in the message box to check before sending (Deepgram, or the browser's own recognition without a key)

## Stack
Next.js + TypeScript · FastAPI + Pydantic · LangGraph + LangChain (OpenAI) · LangSmith · PostgreSQL 16 (Docker)

## Quickstart
Prerequisites: Docker, [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you), Node 20+. The dev Postgres listens on **localhost:5433** so it doesn't clash with a local Postgres on 5432.

```bash
cp .env.example .env        # fill in your keys; never commit .env
make install                # backend (uv) + frontend (npm) deps
make up                     # start Postgres (localhost:5433)
make seed                   # migrate + load data/seed + fabricate demo data
make test-db                # separate test database (tests and evals never touch the demo data)
make api                    # FastAPI on http://localhost:8000
make web                    # Next.js on http://localhost:3000
make test                   # recreate the test DB, then lint, types and all tests (no LLM calls)
make ask ROLE=owner Q="How are we doing?"   # one chat turn in the terminal
make eval CONFIRM=yes       # 107-case golden eval with the real model (~$0.37)
make converse CONFIRM=yes   # 36 multi-turn conversations with automatic checks (~$0.15)
```

`LLM_MODE=fake` (the default) runs everything offline with scripted models: no OpenAI spend. Set `LLM_MODE=openai` in `.env` to use the real model.

### Environment variables
See [.env.example](.env.example). The required ones are `DATABASE_URL`, `JWT_SECRET_KEY`, `OPENAI_API_KEY` and `LANGSMITH_API_KEY`. Secrets live only in `.env` locally and in the deployment platform's secret store.

### Demo logins (created by `make seed`)
| Role | Email |
|---|---|
| Owner | `owner@demo.inventorymantra` |
| Procurement Manager | `procurement@demo.inventorymantra` |
| Inventory Manager | `inventory@demo.inventorymantra` |

The password is printed once by `make seed`, or set via `DEMO_PASSWORD`.

## Demo script (about 10 minutes, `LLM_MODE=openai`)
| # | Log in as | Do | What it shows |
|---|---|---|---|
| 1 | Inventory Manager | "How many MS Bush OD40 do we have and where?" | messy name → the right item, answer from the data |
| 2 | Inventory Manager | "stock of bush" → pick one | asks back when a name matches several items |
| 3 | Inventory Manager | "कौन से आइटम खत्म होने वाले हैं?" | Hindi in, Hindi out, table chosen automatically |
| 4 | Inventory Manager | "What price did we pay for SS sheet?" · "Show me the vendors" | the fixed denial, nothing revealed |
| 5 | Inventory Manager | "Add 3 AC Contactor to 132-2" → Confirm | preview card, confirm, audit; the stock changes |
| 6 | Procurement Manager | "Which POs are overdue?" · "kaun se PO late hain?" | same answer in English and Hinglish |
| 7 | Procurement Manager | "Will PO-2026-0003 arrive tomorrow?" | no prediction: recorded date and days overdue |
| 8 | Procurement Manager | "Place a PO for 200 AC Contactor" → vendor → price | one question at a time; above ₹1,00,000 it goes to the Owner |
| 9 | Owner | "How are we doing?" → Sales and margin | fixed measure options, line chart by quarter |
| 10 | Owner | Approvals page: ask about the PO, then approve | the PM gets a notification and can reply |
| 11 | Owner | "What is Elon Musk's net worth?" · "stock of bush and write a poem" | out-of-scope handling, data part still answered |
| 12 | any | click the mic, say "kaun se PO late hain", check the text, Send | voice goes through the same pipeline |
| 13 | any | open LangSmith (`LANGSMITH_PROJECT`) | one trace per turn, tagged role / language / route |

## Documentation
- [plan.md](plan.md): plan, phases, commit protocol, Definition of Done
- [CLAUDE.md](CLAUDE.md): engineering and agent rules
- [docs/TECHNICAL_GUIDE.md](docs/TECHNICAL_GUIDE.md): **start here**: how it works (LangGraph / LangChain / LangSmith with file names), prompts, golden eval set, tests, results, limitations
- [docs/Architecture.md](docs/Architecture.md): diagrams, and where LangGraph, LangChain and LangSmith sit
- [docs/DECISION.md](docs/DECISION.md): decision log
- [docs/QUALITY_AND_EVALS.md](docs/QUALITY_AND_EVALS.md): eval corpus, metrics, failure behaviour
- [docs/WRITE_PATH.md](docs/WRITE_PATH.md): how database writes are built and guarded
- [docs/PROMPTS.md](docs/PROMPTS.md): agent prompts (versioned; changes are reviewed)

## Data note
The data is seeded from a snapshot of the manufacturer's database (inventory and procurement tables only). Reorder policies, lead times and sales/margin data are **fabricated demo data** (deterministic, labelled `source='demo'`) so that alerts and Owner financials can be shown.

## Roadmap
- Out of scope for the case study: deployment (runs on localhost), LangSmith dataset sync of the golden set.
