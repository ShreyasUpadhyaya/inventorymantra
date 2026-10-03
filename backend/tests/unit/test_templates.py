"""Template answers read like sentences, in all three languages (D-040)."""

from app.agent.grounding import check_grounding
from app.agent.prediction import banned_span
from app.agent.templates import template_answer
from app.agent.viz import choose_viz

COLUMNS = ["po_number", "vendor_name", "status", "total_amount", "expected_delivery_date",
           "days_overdue"]  # fmt: skip
ROW = ["PO-2026-0003", "Falcon Steels Pvt. Ltd.", "placed", 50986.0, "2026-08-13", 48]


def test_english_record_sentence() -> None:
    assert template_answer("en", COLUMNS, [ROW], "Status of PO 3?") == (
        "PO-2026-0003 from Falcon Steels Pvt. Ltd. is placed and worth ₹50,986. "
        "Its expected delivery date is 13 Aug 2026. It is 48 days overdue."
    )


def test_hindi_and_hinglish_read_naturally_and_stay_grounded() -> None:
    hi = template_answer("hi", COLUMNS, [ROW], "PO 3 का स्टेटस?")
    hing = template_answer("hinglish", COLUMNS, [ROW], "PO 3 ka status?")
    assert "अभी placed है" in hi and "48 दिन लेट है" in hi and "13 Aug 2026" in hi
    assert "abhi placed hai" in hing and "48 din late hai" in hing
    for text, question in ((hi, "PO 3 का स्टेटस?"), (hing, "PO 3 ka status?")):
        assert check_grounding(text, COLUMNS, [ROW], [question]).ok, text


def test_one_day_is_singular_and_no_double_full_stop() -> None:
    row = ["PO-2026-0009", "Granite Steels Pvt. Ltd.", 1]
    text = template_answer("en", ["po_number", "vendor_name", "days_overdue"], [row], "overdue?")
    assert text == "PO-2026-0009 from Granite Steels Pvt. Ltd.: overdue by 1 day."
    assert ".." not in text


def test_metric_rows_and_lists() -> None:
    assert template_answer("en", ["open_pos"], [[36]], "How many open POs?") == "Open pos is 36."
    many = template_answer("en", COLUMNS, [ROW, ROW, ROW], "Which POs are overdue?")
    assert many.startswith("I found 3 matching records. The first one: PO-2026-0003")
    assert check_grounding(many, COLUMNS, [ROW, ROW, ROW], ["Which POs are overdue?"]).ok


def test_future_questions_get_the_honest_prefix_in_every_language() -> None:
    for lang, question, prefix in (
        ("en", "Will PO-2026-0003 arrive tomorrow?", "I can't predict"),
        ("hinglish", "PO-2026-0009 kal tak aa jayega kya?", "Aage kya hoga"),
        ("hi", "क्या PO-2026-0009 कल तक आएगा?", "आगे क्या होगा"),
    ):
        text = template_answer(lang, COLUMNS, [ROW], question)  # type: ignore[arg-type]
        assert text.startswith(prefix) and banned_span(text) is None, text


def test_no_rows_and_unknown_columns() -> None:
    assert template_answer("en", ["x"], [], "anything") == "No matching records were found."
    assert template_answer("en", ["a_b"], [[None]], "q") == "A b is not recorded."


def test_lookups_get_no_cards_but_figures_do() -> None:
    cols = ["po_number", "status", "days_overdue"]
    assert (
        choose_viz("Will PO-2026-0003 arrive tomorrow?", cols, [["PO-1", "placed", 48]]).type
        == "none"
    )
    assert (
        choose_viz("How many days overdue is PO 3?", cols, [["PO-1", "placed", 48]]).type == "kpi"
    )
    assert choose_viz("What's the total value?", ["total_value"], [[1.0]]).type == "kpi"
    assert choose_viz("Show the lines on PO 3", ["a", "b", "c"], [["x", "y", 1]]).type == "table"
