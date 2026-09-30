# InventoryMantra

**A bilingual (English / हिन्दी) inventory and procurement assistant for a railway-equipment manufacturer.**

Ask questions in plain language, like "Which POs are overdue?" or "पिछले महीने 132-1 में कितना माल आया?". InventoryMantra turns them into safe SQL, answers only from the data, asks back when a question is vague, and picks the right chart on its own. What you can see and change depends on your role: Inventory Manager, Procurement Manager or Owner.

> Status: **Phase 0: planning.** See [plan.md](plan.md) for progress.

## Features
- 💬 **Chat over the database**: NL → SQL → NL, in English, Hindi or Hinglish
- ❓ **Clarifies vague questions** ("a few days back?" → offers concrete date ranges)
- 📊 **Automatic visuals**: KPI, table, bar, line, pie, stacked or flow diagram, chosen per question
- 🔐 **Role-based access**, enforced by DB role views and read-only DB users, not just prompts
- ✍️ **Writes by chat** (bonus): stock movements, stock requests, purchase orders, with preview → confirm → Owner approval → audit
- 🔔 **Role-scoped notifications** and **lead-time reorder alerts**
- 📈 **Role dashboards**: stock and shortages (IM), pipeline and reorder list (PM), everything plus spend and margins (Owner)
- 🔍 **LangSmith tracing and evals** on every agent run

## Stack
Next.js + TypeScript · FastAPI + Pydantic · LangGraph + LangChain (OpenAI) · LangSmith · PostgreSQL 16 (Docker)

## Quickstart
> These commands are available from Phase 1 onwards.

Prerequisites: Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cp .env.example .env        # fill in your keys; never commit .env
make up                     # start Postgres
make seed                   # migrate + load data/seed + fabricate demo data
make api                    # FastAPI on http://localhost:8000
make web                    # Next.js on http://localhost:3000
make test                   # lint, types, unit + RBAC tests (no LLM calls)
make eval                   # LangSmith eval run (uses API credits)
```

### Environment variables
See [.env.example](.env.example). The required ones are `DATABASE_URL`, `JWT_SECRET_KEY`, `OPENAI_API_KEY` and `LANGSMITH_API_KEY`. Secrets live only in `.env` locally and in the deployment platform's secret store.

### Demo logins (created by `make seed`)
| Role | Email |
|---|---|
| Owner | `owner@demo.inventorymantra` |
| Procurement Manager | `procurement@demo.inventorymantra` |
| Inventory Manager | `inventory@demo.inventorymantra` |

The password is printed once by `make seed`, or set via `DEMO_PASSWORD`.

## Documentation
- [plan.md](plan.md): plan, phases, commit protocol, Definition of Done
- [CLAUDE.md](CLAUDE.md): engineering and agent rules
- [docs/Architecture.md](docs/Architecture.md): diagrams, and where LangGraph, LangChain and LangSmith sit
- [docs/DECISION.md](docs/DECISION.md): decision log
- [docs/QUALITY_AND_EVALS.md](docs/QUALITY_AND_EVALS.md): eval corpus, metrics, failure behaviour
- [docs/WRITE_PATH.md](docs/WRITE_PATH.md): how database writes are built and guarded
- [docs/PROMPTS.md](docs/PROMPTS.md): agent prompts (versioned; changes are reviewed)

## Data note
The data is seeded from a snapshot of the manufacturer's database (inventory and procurement tables only). Reorder policies, lead times and sales/margin data are **fabricated demo data** (deterministic, labelled `source='demo'`) so that alerts and Owner financials can be shown.

## Roadmap
- 🎙️ Voice input in Hindi and English (Deepgram free tier / local faster-whisper), feeding the same chat pipeline.
