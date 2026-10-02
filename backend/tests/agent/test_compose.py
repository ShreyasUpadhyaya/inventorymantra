"""Answer composer (C4.4): charts, grounded prose, no predictions, safe fallback. Offline."""

from typing import Any

import pytest
from langchain_core.messages import BaseMessage

from app.agent import fake
from app.agent.formatting import inr, show
from app.agent.grounding import check_grounding
from app.agent.nodes.compose import template_answer
from app.agent.prediction import banned_span
from app.agent.prompts import DENIAL
from app.agent.run import run_turn
from app.agent.viz import choose_viz, po_flow_mermaid
from app.auth.service import AuthUser
from app.core.config import get_settings
from evals.schema import EvalCase, load_corpus
from scripts.fabricate import DEMO_USERS, demo_id

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"
READS = [c for c in load_corpus() if c.expected_route == "read" and not c.history]
OVERDUE = "Which purchase orders are overdue?"


@pytest.fixture(autouse=True)
def _needs_db(db: object) -> None:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")


def demo(role: str) -> AuthUser:
    email = next(e for e, _, r in DEMO_USERS if r == role)
    return AuthUser(id=demo_id("user", email), email=email, full_name="Demo", role=role,  # type: ignore[arg-type]
                    is_active=True, token_version=0)  # fmt: skip


def composer(*answers: str) -> tuple[Any, list[list[BaseMessage]]]:
    """Script the composer with successive answers; returns (context manager, calls)."""
    calls: list[list[BaseMessage]] = []
    queue = list(answers)

    def handler(messages: list[BaseMessage]) -> str:
        calls.append(messages)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return fake.scripted("compose", handler), calls


# ------------------------------------------------------------------ the corpus, end to end
@pytest.mark.parametrize("case", READS, ids=lambda c: c.id)
def test_chart_choice_on_corpus_questions(case: EvalCase) -> None:
    result = run_turn(demo(case.role), case.query)
    assert result.viz is not None
    if case.expected_viz:
        assert result.viz["type"] in case.expected_viz


@pytest.mark.parametrize("case", READS, ids=lambda c: c.id)
def test_template_answers_are_grounded_and_factual(case: EvalCase) -> None:
    result = run_turn(demo(case.role), case.query)
    assert result.answer_checks.get("fallback") is True  # offline: the template answered
    context = [
        case.query,
        *(f"{r['code']} {r['name']}" for r in result.resolved),
    ]  # as the app does
    grounding = check_grounding(result.answer, result.columns, result.rows, context)
    assert grounding.ok, grounding.unsupported
    assert banned_span(result.answer) is None


# ------------------------------------------------------------------ composer checks
GOOD = "28 purchase orders are overdue; the oldest is PO-2026-0009 at 53 days past its date."


def test_grounded_answer_is_used_as_written() -> None:
    script, calls = composer(GOOD)
    with script:
        result = run_turn(demo(PM), OVERDUE)
    assert result.answer == GOOD and len(calls) == 1
    assert result.answer_checks == {"fallback": False, "problems": []}


def test_invented_number_is_regenerated_once() -> None:
    script, calls = composer("777 purchase orders are overdue.", GOOD)
    with script:
        result = run_turn(demo(PM), OVERDUE)
    assert len(calls) == 2 and result.answer == GOOD


def test_two_bad_answers_fall_back_to_the_template() -> None:
    script, calls = composer("777 POs are overdue.", "Probably 31 POs will arrive late.")
    with script:
        result = run_turn(demo(PM), OVERDUE)
    assert len(calls) == 2
    assert result.answer_checks["fallback"] is True
    assert result.answer.startswith("Found 28 matching records.")
    assert any(p.startswith(("ungrounded", "predictive")) for p in result.answer_checks["problems"])


@pytest.mark.parametrize(
    "answer",
    [
        "PO-2026-0003 will probably arrive next week.",
        "Stock should last until October.",
        "PO-2026-0009 shayad kal tak aa jayega.",
        "PO-2026-0009 शायद कल आ जाएगा।",
        "Spend is projected to rise.",
    ],
)
def test_predictions_are_rejected(answer: str) -> None:
    script, _ = composer(answer)
    with script:
        result = run_turn(demo(PM), OVERDUE)
    assert result.answer_checks["fallback"] is True
    assert result.answer != answer


def test_judge_runs_only_on_future_wording() -> None:
    judged: list[str] = []

    def judge(messages: list[BaseMessage]) -> dict[str, Any]:
        judged.append(str(messages[-1].content))
        return {"predictive": True, "span": "next month"}

    with fake.scripted("judge", judge):
        script, _ = composer("28 POs are overdue; next month's total will match.", GOOD)
        with script:
            result = run_turn(demo(PM), OVERDUE)
        assert len(judged) == 1 and result.answer == GOOD  # first rejected by the judge
        script, _ = composer(GOOD)
        with script:
            run_turn(demo(PM), OVERDUE)
        assert len(judged) == 1  # plain facts never pay for a judge call


def test_denial_is_appended_exactly_once_for_mixed_requests() -> None:
    script, _ = composer(f"MS Bush OD40: 100 in 132-1. {DENIAL['hinglish']}")
    with script:
        result = run_turn(demo(IM), "MS Bush OD40 ka stock aur uska price batao")
    assert result.answer.count(DENIAL["hinglish"]) == 1


def test_composer_receives_data_assumptions_and_language() -> None:
    script, calls = composer(GOOD)
    with script:
        run_turn(demo(PM), "कौन से पर्चेज ऑर्डर लेट हैं?")
    system, user = str(calls[0][0].content), str(calls[0][-1].content)
    assert "Reply in Hindi (Devanagari script)" in system
    assert '<data columns="po_number, vendor_name, expected_delivery_date, days_overdue' in user
    assert "PO-2026-0009" in user and "<denied_parts>" not in user


# ------------------------------------------------------------------ units
def test_indian_money_format() -> None:
    assert inr(4789288.83) == "₹47,89,288.83"
    assert inr(100000) == "₹1,00,000"
    assert inr(999) == "₹999"
    assert show("expected_delivery_date", "2026-08-08") == "8 Aug 2026"
    assert show("margin_pct", 20.9) == "20.9%"
    assert show("vendor_name", None) == "not recorded"


def test_lifecycle_flow_uses_live_counts() -> None:
    mermaid = po_flow_mermaid(["status", "pos"], [["placed", 33], ["received", 21], ["partial", 3]])
    assert mermaid.startswith("flowchart LR")
    assert 'placed["Placed (33)"]' in mermaid and 'received["Received (21)"]' in mermaid
    assert 'pending_approval["Pending approval (0)"]' in mermaid


def test_explicit_chart_requests_are_honoured_when_possible() -> None:
    rows = [["132-1", 266], ["132-3", 74], ["132-2", 51]]
    assert choose_viz("pie please", ["location_name", "n"], rows, "pie").type == "pie"
    assert choose_viz("as a table", ["location_name", "n"], rows, "table").type == "table"
    assert choose_viz("bar", ["a", "b"], [["x", "y"]], "bar").type == "kpi"  # nothing to plot
    assert choose_viz("anything", ["n"], [], None).type == "none"


def test_template_answer_shapes() -> None:
    state: Any = {"lang": "en", "columns": ["open_value", "open_pos"], "rows": [[4789288.83, 36]]}
    assert template_answer(state) == "Result — open value: ₹47,89,288.83; open pos: 36."
    state = {"lang": "hi", "columns": ["x"], "rows": []}
    assert template_answer(state) == "कोई मिलता-जुलता रिकॉर्ड नहीं मिला।"


def test_answers_name_the_matched_record() -> None:
    """D-026 / smoke dry run: 'bush OD40 kitne hain?' must name MS Bush – OD40×ID26.5×70mm."""
    result = run_turn(demo(IM), "bush OD40 kitne hain?")
    assert result.answer.startswith("MS Bush – OD40×ID26.5×70mm ke liye: ")
    script, calls = composer("132-1 mein 100 pcs hain.")
    with script:
        modelled = run_turn(demo(IM), "bush OD40 kitne hain?")
    assert modelled.answer == "MS Bush – OD40×ID26.5×70mm ke liye: 132-1 mein 100 pcs hain."
    assert "MS Bush – OD40×ID26.5×70mm" in str(calls[0][-1].content)  # the model is told too
