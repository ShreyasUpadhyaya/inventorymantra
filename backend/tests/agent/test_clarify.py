"""Clarification as a LangGraph interrupt (C4.3): pause with options, resume in place.

Offline (fake stand-ins / scripted answers); needs the seeded DB for entity lookups.
"""

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from app.agent import fake
from app.agent.run import ThreadAccessError, pending_question, resume_turn, run_turn
from app.auth.service import AuthUser
from app.core.config import get_settings
from evals.schema import EvalCase, load_corpus
from scripts.fabricate import DEMO_USERS, demo_id

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
CLARIFY_CASES = [
    c for c in load_corpus()
    if c.expected_route in ("clarify", "not_found") and not c.history and "write" not in c.tags
]  # fmt: skip


@pytest.fixture(autouse=True)
def _needs_db(db: object) -> None:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")


def demo(role: str) -> AuthUser:
    email = next(e for e, _, r in DEMO_USERS if r == role)
    return AuthUser(id=demo_id("user", email), email=email, full_name="Demo", role=role,  # type: ignore[arg-type]
                    is_active=True, token_version=0)  # fmt: skip


@pytest.fixture
def sql_seen() -> Iterator[list[str]]:
    """Scripted SQL node: builds a query from the resolved item code it receives."""
    seen: list[str] = []

    def handler(messages: list[BaseMessage]) -> dict[str, Any]:
        user = str(messages[-1].content)
        seen.append(user)
        data = json.loads(user.split("<data>")[1].split("</data>")[0])
        codes = [d["code"] for d in data if d["type"] == "item"]
        base = "SELECT location_name, quantity FROM stock_by_location WHERE item_code = "
        sql = f"{base}'{codes[0]}'" if codes else "SELECT count(*) AS n FROM purchase_orders"
        return {"sql": sql, "assumptions": [], "answerable": True}

    with fake.scripted("sql", handler):
        yield seen


# ------------------------------------------------------------------ corpus behaviour
@pytest.mark.parametrize("case", CLARIFY_CASES, ids=lambda c: c.id)
def test_corpus_ask_back_and_not_found_cases(case: EvalCase) -> None:
    result = run_turn(demo(case.role), case.query)
    if case.expected_route == "not_found":
        assert result.route == "read" and result.interrupt is None
        assert result.answer.startswith("I couldn't find")
        for text in case.must_include:
            assert text in result.suggestions
        return
    assert result.route == "clarify"
    assert case.clarify_expect is not None and result.interrupt is not None
    assert result.interrupt["dimension"] == case.clarify_expect.dimension
    assert len(result.suggestions) >= case.clarify_expect.min_options
    assert result.rows == [] and result.sql is None  # nothing ran while paused
    assert pending_question(result.thread_id) == result.interrupt


# ------------------------------------------------------------------ entity: pick and continue
@pytest.mark.parametrize(
    ("answer", "code"),
    [
        ("MS Bush – OD42×ID26.5×67mm", "BO-MC-0101"),  # the option text
        ("2", None),  # its number (whichever option is second)
        ("BO-MC-0100", "BO-MC-0100"),  # its code
        ("OD40", "BO-MC-0100"),  # a fragment that fits one option
        ("od42", "BO-MC-0101"),
    ],
)
def test_entity_choice_resumes_and_reaches_sql(
    sql_seen: list[str], answer: str, code: str | None
) -> None:
    first = run_turn(demo(IM), "stock of bush")
    assert first.route == "clarify" and len(first.suggestions) == 2
    final = resume_turn(demo(IM), first.thread_id, answer)
    assert final.route == "read" and final.interrupt is None
    expected = code or ("BO-MC-0100" if "OD40" in first.suggestions[1] else "BO-MC-0101")
    assert [r["code"] for r in final.resolved] == [expected]
    assert f"'{expected}'" in (final.sql or "")
    assert final.row_count >= 0 and len(sql_seen) == 1


def test_unusable_entity_answer_asks_again_then_gives_up(sql_seen: list[str]) -> None:
    """User-found bug (2026-10-03): nonsense at the prompt used to end the turn at once."""
    first = run_turn(demo(IM), "stock of bush")
    second = resume_turn(demo(IM), first.thread_id, "qwefqwegwe")
    assert second.route == "clarify"  # asked again, same options, saying why
    assert second.answer.startswith("I didn't recognise “qwefqwegwe”.")
    assert "“bush” matches more than one record" in second.answer
    assert second.suggestions == first.suggestions
    final = resume_turn(demo(IM), second.thread_id, "zzzz")
    assert final.route == "read" and final.interrupt is None
    assert final.answer.startswith("I couldn't tell which one you meant for “bush”")
    assert final.suggestions == first.suggestions
    assert final.resolved == [] and sql_seen == []  # never guessed a record


def test_a_good_answer_after_a_bad_one_still_works(sql_seen: list[str]) -> None:
    first = run_turn(demo(IM), "stock of bush")
    second = resume_turn(demo(IM), first.thread_id, "no idea")
    final = resume_turn(demo(IM), second.thread_id, "OD42")
    assert [r["code"] for r in final.resolved] == ["BO-MC-0101"]
    assert len(sql_seen) == 1


def test_free_text_naming_the_record_precisely_resolves(sql_seen: list[str]) -> None:
    first = run_turn(demo(IM), "stock of bush")
    final = resume_turn(demo(IM), first.thread_id, "ms bush od40")
    assert [r["code"] for r in final.resolved] == ["BO-MC-0100"]


# ------------------------------------------------------------------ period / measure
def test_time_window_answer_reaches_sql_and_model_is_called_once(sql_seen: list[str]) -> None:
    calls: list[int] = []

    def counting_clarify(messages: list[BaseMessage]) -> Any:
        calls.append(1)
        return fake.fake_clarify(messages)

    with fake.scripted("clarify", counting_clarify):
        first = run_turn(demo(PM), "What did we spend earlier?")
        assert first.route == "clarify" and first.interrupt["dimension"] == "time_window"  # type: ignore[index]
        final = resume_turn(demo(PM), first.thread_id, "2")
    assert len(calls) == 1  # the pause/resume did not repeat the model call
    assert final.route == "read" and final.failure is None
    assert re.search(r"\[Last 30 days \(1 Sep–30 Sep 2026\)\]</user_message>", sql_seen[0])


def test_options_are_in_the_users_language() -> None:
    result = run_turn(demo(IM), "thode din pehle kitna maal aaya tha?")
    assert result.route == "clarify" and result.lang == "hinglish"
    assert result.answer == "Aapka matlab kaun si avadhi se hai?"
    assert result.suggestions[0].startswith("Pichle 7 din (24 Sep–30 Sep 2026)")


def test_measure_options_fit_the_role() -> None:
    owner = run_turn(demo(OW), "How are we doing?")
    im = run_turn(demo(IM), "How are we doing?")
    assert owner.suggestions == ["Sales and margin", "Spend", "Overdue orders", "Low stock"]
    assert im.suggestions == ["Low stock", "Stock by warehouse", "Issues this month"]


def test_clear_questions_are_not_interrupted() -> None:
    result = run_turn(demo(PM), "Which purchase orders are overdue?")
    assert result.route == "read" and result.interrupt is None and result.row_count == 28


# ------------------------------------------------------------------ thread safety
def test_only_the_thread_owner_can_resume() -> None:
    first = run_turn(demo(IM), "stock of bush")
    with pytest.raises(ThreadAccessError):
        resume_turn(demo(PM), first.thread_id, "1")
    assert pending_question(first.thread_id) is not None  # still waiting for its owner


def test_cannot_resume_a_finished_or_unknown_thread() -> None:
    done = run_turn(demo(PM), "Which purchase orders are overdue?")
    with pytest.raises(ThreadAccessError):
        resume_turn(demo(PM), done.thread_id, "1")
    with pytest.raises(ThreadAccessError):
        resume_turn(demo(PM), "no-such-thread", "1")


def test_a_new_question_on_a_paused_thread_starts_clean(sql_seen: list[str]) -> None:
    first = run_turn(demo(IM), "stock of bush")
    again = run_turn(demo(IM), "Which warehouses are active?", thread_id=first.thread_id)
    assert again.route == "read" and again.interrupt is None
    assert again.resolved == []  # nothing carried over from the abandoned question


def test_nonsense_period_answer_is_asked_again_then_refused(sql_seen: list[str]) -> None:
    first = run_turn(demo(PM), "What did we spend earlier?")
    second = resume_turn(demo(PM), first.thread_id, "qwefqwegwe")
    assert second.route == "clarify"
    assert second.answer == "I didn't recognise “qwefqwegwe”. Which period do you mean?"
    assert second.suggestions == first.suggestions
    final = resume_turn(demo(PM), second.thread_id, "banana")
    assert final.failure == "no_choice" and final.interrupt is None
    assert final.answer.startswith("I couldn't tell which period you meant")
    assert sql_seen == []


@pytest.mark.parametrize(
    "answer", ["from 1 Aug to 15 Aug", "August", "last week", "pichle hafte", "पिछले महीने", "3"]
)
def test_free_text_periods_are_accepted(sql_seen: list[str], answer: str) -> None:
    first = run_turn(demo(PM), "What did we spend earlier?")
    final = resume_turn(demo(PM), first.thread_id, answer)
    assert final.route == "read" and final.interrupt is None and len(sql_seen) == 1


def test_offline_choice_after_a_bad_answer_returns_the_chosen_items_stock() -> None:
    """User-found (2026-10-03): picking "2" after nonsense resolved correctly, but offline mode
    then said "not recorded". Offline now answers simple questions about the chosen record."""
    first = run_turn(demo(IM), "stock of bush")
    second = resume_turn(demo(IM), first.thread_id, "gaergaergaewrga")
    final = resume_turn(demo(IM), second.thread_id, "2")
    chosen = first.suggestions[1]
    assert final.failure is None and final.row_count >= 1
    assert final.rows[0][1] == chosen  # item_name column of the chosen bush
    assert "item_code = '" in (final.sql or "")


def test_offline_limits_are_stated_honestly() -> None:
    result = run_turn(demo(PM), "Which vendor has the friendliest sales rep?")
    assert result.failure == "offline"
    assert result.answer.startswith("Offline demo mode (LLM_MODE=fake)")
    assert "isn't recorded" not in result.answer
