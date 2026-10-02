"""Grounding check (DECISION D-010): every number in an answer must come from the rows, or be
a simple sum/average/count/percentage of them, or appear in the user's own words."""

import pytest

from app.agent.grounding import check_grounding, extract_numbers

COLUMNS = ["po_number", "vendor_name", "expected_delivery_date", "days_overdue", "total_amount"]
ROWS = [
    ["PO-2026-0009", "Granite Steels Pvt. Ltd.", "2026-08-08", 53, 36363.6],
    ["PO-2026-0003", "Falcon Steels Pvt. Ltd.", "2026-08-13", 48, 50986.0],
    ["PO-2026-0012", "Shakti Steels Pvt. Ltd.", "2026-08-17", 44, 13532.75],
]


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("₹47,89,288.83 across 36 POs", [4789288.83, 36]),
        ("₹4,789,288.83", [4789288.83]),
        ("about ₹47.9 lakh", [4790000.0]),
        ("₹3.86 crore this year", [38600000.0]),
        ("२८ ऑर्डर लेट हैं", [28]),
        ("₹47.9 लाख", [4790000.0]),
        ("margin was 20.9%", [20.9]),
        ("PO-2026-0009 is 53 days overdue", [2026, 9, 53]),
        ("MS Bush – OD40×ID26.5×70mm", []),  # numbers glued to letters are part of a name
        ("12k units", [12000.0]),
    ],
)
def test_extract_numbers(text: str, numbers: list[float]) -> None:
    assert [round(n, 2) for n in extract_numbers(text)] == numbers


@pytest.mark.parametrize(
    "answer",
    [
        "3 purchase orders are overdue; the oldest is PO-2026-0009 from Granite Steels Pvt. Ltd., "
        "53 days past its expected delivery on 8 Aug 2026.",
        "Their total value is ₹1,00,882.35.",  # sum of total_amount
        "On average they are 48.3 days overdue.",  # mean of 53, 48, 44
        "PO-2026-0003 is about ₹51 thousand.",  # rounded with a multiplier
        "PO-2026-0003 is 50.5% of the overdue value.",  # share of the column total
        "Overdue POs: ३ — सबसे पुराना ५३ दिन।",  # Devanagari digits
        "None of these were placed in 2026-Q4.",  # number taken from the user's question
    ],
)
def test_grounded_answers_pass(answer: str) -> None:
    result = check_grounding(
        answer, COLUMNS, ROWS, context=["Which POs are overdue as of 2026-Q4?"]
    )
    assert result.ok, result.unsupported


@pytest.mark.parametrize(
    ("answer", "bad"),
    [
        ("4 purchase orders are overdue.", "4"),
        ("The oldest is 60 days overdue.", "60"),
        ("Their total value is ₹2,50,000.", "250000"),
        ("Overdue value is ₹12 lakh.", "12 lakh"),
        ("About 75% of POs are late.", "75"),
    ],
)
def test_invented_numbers_are_caught(answer: str, bad: str) -> None:
    result = check_grounding(answer, COLUMNS, ROWS, context=["Which POs are overdue?"])
    assert not result.ok
    assert bad in [u.replace(",", "") for u in result.unsupported]


def test_empty_result_allows_only_context_numbers() -> None:
    ok = check_grounding("No movements were recorded between 21 and 27 Sep 2026.", ["x"], [],
                         context=["last week", "21 Sep 2026 - 27 Sep 2026"])  # fmt: skip
    assert ok.ok
    bad = check_grounding("No movements; usually there are 40 a week.", ["x"], [], context=[])
    assert not bad.ok


def test_date_parts_only_back_numbers_written_as_dates() -> None:
    """Found while testing C4.4: '29 POs are overdue' passed because a date fell on the 29th."""
    rows = [["PO-2026-0040", "2026-08-29", 32], ["PO-2026-0041", "2026-08-31", 34]]
    columns = ["po_number", "expected_delivery_date", "days_overdue"]
    ctx = ["Which POs are overdue?"]
    assert not check_grounding("29 purchase orders are overdue.", columns, rows, ctx).ok
    assert not check_grounding("31 purchase orders are overdue.", columns, rows, ctx).ok
    written_as_dates = [
        "PO-2026-0040 was due on 29 Aug 2026.",
        "Due Aug 31, 2026.",
        "Due 2026-08-29.",
        "Both were due in August 2026.",
        "Due 29–31 Aug.",
        "देय तिथि २९ अगस्त थी।",
    ]
    for ok in written_as_dates:
        assert check_grounding(ok, columns, rows, ctx).ok, ok


def test_count_claims_must_match_a_real_count() -> None:
    """Smoke run 2: '31 purchase orders' when the rows held 28; 31 existed elsewhere in the data."""
    rows = [
        ["PO-2026-0009", "Granite Steels", 53, 31.0],
        ["PO-2026-0003", "Falcon Steels", 48, 9.0],
        ["PO-2026-0012", "Falcon Steels", 44, 2.0],
    ]
    columns = ["po_number", "vendor_name", "days_overdue", "amount"]
    ctx = ["Which POs are overdue?"]
    assert check_grounding("3 purchase orders are overdue.", columns, rows, ctx).ok
    assert check_grounding("2 POs are from Falcon Steels.", columns, rows, ctx).ok  # group count
    assert check_grounding("They come from 2 vendors.", columns, rows, ctx).ok  # distinct count
    bad = check_grounding("31 purchase orders are overdue.", columns, rows, ctx)
    assert not bad.ok and "31 purchase orders" in bad.unsupported
    assert not check_grounding("There are 53 overdue orders.", columns, rows, ctx).ok
    question = ["132-1 में कितने आइटम का स्टॉक शून्य है?"]
    hindi = check_grounding(
        "132-1 में ७८ आइटम का स्टॉक शून्य है।", ["zero_stock_items"], [[78]], question
    )
    assert hindi.ok
