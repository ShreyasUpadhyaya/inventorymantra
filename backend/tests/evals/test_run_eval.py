"""The eval runner's scoring (C7.1), on hand-made results: no model, no database."""

from types import SimpleNamespace
from typing import Any

from evals.run_eval import gates, metrics, route_of
from evals.smoke import visible_text


def result(**kw: Any) -> SimpleNamespace:
    base = {"route": "read", "answer": "", "suggestions": [], "columns": [], "rows": [],
            "interrupt": None, "lang": "en", "viz": None, "resolved": [],
            "answer_checks": {}}  # fmt: skip
    return SimpleNamespace(**{**base, **kw})


def case(route: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(expected_route=route, write_expect=kw.get("write_expect"))


def test_routes_for_write_cases() -> None:
    card = result(interrupt={"kind": "confirm", "previews": [{"action": "x"}]})
    assert route_of(case("write_preview"), card) == "write_preview"
    refused = SimpleNamespace(expect_error="insufficient_stock")
    assert (
        route_of(case("write_preview", write_expect=refused), result(route="write"))
        == "write_preview"
    )
    assert route_of(case("not_found"), result(answer="I couldn't find x")) == "not_found"


def test_only_what_the_user_sees_can_leak() -> None:
    hidden = result(answer="That information isn't available for your role.")
    hidden.denied_areas = ["vendors"]  # internal field, never shown
    assert "vendors" not in visible_text(hidden)
    shown = result(answer="We have 34 vendors.")
    assert "34" in visible_text(shown)


def test_metrics_and_gates() -> None:
    cases = {"A": case("read"), "B": case("refuse_rbac"), "C": case("clarify"),
             "D": case("refuse_scope")}  # fmt: skip
    rows = [
        {"id": "A", "error": None, "seconds": 2.0, "actual_route": "read",
         "checks": {"route": True, "lang": True, "answer_key": True, "viz": True,
                    "no_prediction": True, "faithful": True}},
        {"id": "B", "error": None, "seconds": 0.5, "actual_route": "read",  # answered: a leak
         "checks": {"route": False, "lang": True, "no_prediction": True}},
        {"id": "C", "error": None, "seconds": 0.5, "actual_route": "clarify",
         "checks": {"route": True, "lang": True, "no_prediction": True}},
        {"id": "D", "error": None, "seconds": 0.5, "actual_route": "refuse_scope",
         "checks": {"route": True, "lang": True, "no_prediction": True}},
    ]  # fmt: skip
    m = metrics(rows, cases)
    assert m["rbac_leaks"] == 1 and m["route_accuracy"] == 0.75
    assert m["clarify_recall"] == 1.0 and m["clarify_precision"] == 1.0
    g = gates(m)
    assert g["rbac_leaks"] is False and g["route_accuracy"] is False
    assert g["execution_accuracy"] is True and g["scope_refusal"] is True


def test_a_count_matches_the_number_of_rows_and_buttons_count_as_shown_text() -> None:
    from evals.smoke import _numbers_covered

    assert _numbers_covered([[74]], [["a", 1]] * 74, row_count=74)
    assert not _numbers_covered([[74]], [["a", 1]] * 70, row_count=70)
    shown = result(answer="Did you mean one of these?", suggestions=["Hexalobular Screw"])
    assert "Hexalobular Screw" in visible_text(shown)
