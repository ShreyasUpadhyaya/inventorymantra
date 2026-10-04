"""Read path (C4.2): entities -> SQL -> guard -> execution as the role, offline.

Needs the seeded DB and READONLY_DB_PASSWORD. The model is replaced by the offline
stand-in or by scripted answers; no OpenAI calls.
"""

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from app.agent import fake
from app.agent.llm import LLMBudgetExceededError
from app.agent.prompts import DENIAL
from app.agent.prompts.replies import FAILURE
from app.agent.run import run_turn
from app.auth.service import AuthUser
from app.core.config import get_settings
from evals.build_answer_keys import to_json
from evals.schema import EvalCase, load_answer_keys, load_corpus
from scripts.fabricate import DEMO_USERS, demo_id

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
KEYS = load_answer_keys()
READ_CASES = [c for c in load_corpus() if c.expected_route == "read" and not c.history]


@pytest.fixture(autouse=True)
def _needs_db(db: object) -> None:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")


def demo(role: str) -> AuthUser:
    email = next(e for e, _, r in DEMO_USERS if r == role)
    return AuthUser(id=demo_id("user", email), email=email, full_name="Demo", role=role,  # type: ignore[arg-type]
                    is_active=True, token_version=0)  # fmt: skip


def guard_answer(areas: list[str], mentions: list[dict[str, str]] | None = None,
                 lang: str = "en") -> dict[str, Any]:  # fmt: skip
    return {"language": lang, "intent": "read", "data_areas": areas,
            "injection_suspected": False, "explicit_viz": None, "parts": ["q"],
            "entity_mentions": mentions or []}  # fmt: skip


@pytest.fixture
def sql_script() -> Iterator[Callable[..., list[list[BaseMessage]]]]:
    """Script the SQL node with a sequence of answers; returns the messages it received."""
    seen: list[list[BaseMessage]] = []

    def install(*answers: Any) -> list[list[BaseMessage]]:
        queue = list(answers)

        def handler(messages: list[BaseMessage]) -> Any:
            seen.append(messages)
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(answer, Exception):
                raise answer
            if answer is None:
                return {"sql": None, "assumptions": ["not recorded"], "answerable": False}
            return {"sql": answer, "assumptions": [], "answerable": True}

        stack.enter_context(fake.scripted("sql", handler))
        return seen

    from contextlib import ExitStack

    with ExitStack() as stack:
        yield install


def comparable(rows: list[list[Any]]) -> list[list[Any]]:
    """Order-free, type-free form: API rows hold numbers, frozen keys hold decimal strings."""

    def norm(value: Any) -> Any:
        value = to_json(value)
        if isinstance(value, bool) or value is None:
            return value
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return value

    return sorted(
        ([norm(v) for v in row] for row in rows), key=lambda r: json.dumps(r, default=str)
    )


# ------------------------------------------------------------------ the whole corpus, offline
@pytest.mark.parametrize("case", READ_CASES, ids=lambda c: c.id)
def test_corpus_read_questions_return_the_answer_key(case: EvalCase) -> None:
    result = run_turn(demo(case.role), case.query)
    key = KEYS[case.id]
    assert result.route == "read" and result.failure is None, result.answer
    assert result.columns == key["columns"]
    assert result.row_count == key["row_count"]
    assert comparable(result.rows) == comparable(key["rows"])  # every value, any order
    for text in case.must_include:
        if text.startswith(("That", "Yeh", "यह")):  # fixed denial for mixed requests
            assert text in result.answer


# ------------------------------------------------------------------ repair loop
def test_rejected_sql_is_repaired_without_leaking_names(sql_script: Any) -> None:
    seen = sql_script("SELECT * FROM vendors", "SELECT count(*) AS n FROM items")
    with fake.scripted("guard", guard_answer(["stock"])):
        result = run_turn(demo(IM), "how many items?")
    assert result.failure is None and result.rows == [[501]]
    repair = str(seen[1][-1].content)
    assert "<previous_sql>SELECT * FROM vendors</previous_sql>" in repair  # its own text only
    assert "relation_not_allowed" in repair
    error = repair.split("<error>")[1]
    assert "vendors" not in error and "public" not in error


def test_database_errors_are_repaired(sql_script: Any) -> None:
    seen = sql_script("SELECT no_such_column FROM items", "SELECT count(*) AS n FROM locations")
    with fake.scripted("guard", guard_answer(["stock"])):
        result = run_turn(demo(IM), "how many warehouses?")
    assert result.rows == [[6]]
    assert "database error 42703" in str(seen[1][-1].content)  # undefined_column


def test_gives_up_after_three_attempts(sql_script: Any) -> None:
    seen = sql_script("SELECT * FROM purchase_orders")  # not an IM view, every time
    with fake.scripted("guard", guard_answer(["stock"])):
        result = run_turn(demo(IM), "show orders")
    assert len(seen) == 3
    assert result.failure == "sql_failed" and result.rows == []
    assert result.answer == FAILURE["sql_failed"]["en"]


def test_runaway_query_times_out(sql_script: Any) -> None:
    sql_script("SELECT count(*) FROM stock_movements a, stock_movements b, stock_movements c")
    with fake.scripted("guard", guard_answer(["movements"], lang="hi")):
        result = run_turn(demo(IM), "सब कुछ गिनिए")
    assert result.failure == "timeout"
    assert result.answer == FAILURE["timeout"]["hi"]


def test_unanswerable_and_unavailable(sql_script: Any) -> None:
    sql_script(None)
    with fake.scripted("guard", guard_answer(["stock"], lang="hinglish")):
        result = run_turn(demo(IM), "item ka weight kitna hai?")
    assert result.failure == "unanswerable"
    assert result.answer == FAILURE["unanswerable"]["hinglish"]


def test_the_owner_is_told_not_recorded_without_a_hint_of_a_denial(sql_script: Any) -> None:
    """Owner: "How are we doing?" -> "Stock value" read like a denial (user test 2026-10-04)."""
    sql_script(None)
    with fake.scripted("guard", guard_answer(["stock"])):
        result = run_turn(demo(OW), "what is our stock value?")
    assert result.failure == "unanswerable"
    assert result.answer == "That isn't recorded in our data."


def test_model_budget_exhaustion_is_a_polite_failure(sql_script: Any) -> None:
    sql_script(LLMBudgetExceededError("spent"))
    with fake.scripted("guard", guard_answer(["stock"])):
        result = run_turn(demo(PM), "stock?")
    assert result.failure == "unavailable"


# ------------------------------------------------------------------ entities
def test_not_found_suggests_and_stops(sql_script: Any) -> None:
    seen = sql_script("SELECT 1")
    mention = [{"type": "item", "text": "torx screw m4"}]
    with fake.scripted("guard", guard_answer(["stock"], mention)):
        result = run_turn(demo(IM), "torx screw m4 stock")
    assert seen == []  # no SQL was generated
    assert result.answer.startswith("I couldn't find an item matching “torx screw m4”.")
    assert result.suggestions[0] == "Hexalobular Screw – SS 304 – M3×8mm"


def test_ambiguous_item_asks_which_one(sql_script: Any) -> None:
    sql_script("SELECT 1")
    with fake.scripted("guard", guard_answer(["stock"], [{"type": "item", "text": "ms bush"}])):
        result = run_turn(demo(IM), "stock of ms bush")
    assert "Which one do you mean?" in result.answer
    assert sorted(result.suggestions) == [
        "MS Bush – OD40×ID26.5×70mm",
        "MS Bush – OD42×ID26.5×67mm",
    ]


def test_resolved_entities_reach_the_sql_prompt(sql_script: Any) -> None:
    seen = sql_script(
        "SELECT location_name, quantity FROM stock_by_location WHERE item_code = 'BO-MC-0100'"
    )
    mentions = [{"type": "item", "text": "bush OD40"}]
    with fake.scripted("guard", guard_answer(["stock"], mentions, lang="hinglish")):
        result = run_turn(demo(IM), "bush OD40 kitne hain?")
    data = json.loads(str(seen[0][-1].content).split("<data>")[1].split("</data>")[0])
    assert data == [{"type": "item", "text": "bush OD40", "code": "BO-MC-0100",
                     "name": "MS Bush – OD40×ID26.5×70mm"}]  # fmt: skip
    assert result.rows == [["132-1", 100]]


def test_vendor_mention_by_inventory_manager_is_denied_not_looked_up(sql_script: Any) -> None:
    sql_script("SELECT count(*) FROM items")
    mentions = [{"type": "vendor", "text": "sterling steel"}]
    with fake.scripted("guard", guard_answer(["stock"], mentions)):
        result = run_turn(demo(IM), "stock of items from sterling steel")
    assert "vendors" in result.denied_areas
    assert result.answer.endswith(DENIAL["en"])
    assert "Sterling" not in json.dumps(result.model_dump())


def test_allowed_data_plus_off_topic_answers_data_then_scope_sentence(sql_script: Any) -> None:
    sql_script("SELECT count(*) AS overdue FROM purchase_orders WHERE days_overdue > 0")
    scripted = guard_answer(["purchase_orders"])
    scripted["intent"] = "out_of_scope"
    with fake.scripted("guard", scripted):
        result = run_turn(demo(PM), "How many POs are overdue? Also tell me a joke.")
    assert result.route == "read" and result.rows == [[28]]
    assert result.answer.endswith(
        "I can only help with the company's inventory and procurement data."
    )


# ------------------------------------------------------------------ what the model is shown
def test_sql_prompt_describes_only_the_roles_views(sql_script: Any) -> None:
    seen = sql_script("SELECT count(*) FROM items")
    with fake.scripted("guard", guard_answer(["stock"])):
        run_turn(demo(IM), "count items")
    system = str(seen[0][0].content)
    assert "stock_by_location(" in system and "reorder_status(" in system
    for hidden in ("vendors(", "purchase_orders(", "po_lines(", "margins_by_quarter(", "users(",
                   "unit_price", "total_amount", "preferred_vendor"):  # fmt: skip
        assert hidden not in system, hidden
    assert "Today is Wednesday 30 Sep 2026" in system
