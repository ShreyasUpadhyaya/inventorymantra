"""The golden corpus is well-formed, covers what QUALITY_AND_EVALS.md promises, and its
frozen answer keys still match the seeded database."""

from collections import Counter

import pytest

from app.core.config import get_settings
from evals.build_answer_keys import compute_key
from evals.schema import DENIAL, EvalCase, load_answer_keys, load_corpus

CORPUS = load_corpus()
KEYS = load_answer_keys()
# Write tools from docs/WRITE_PATH.md §4.
KNOWN_TOOLS = {
    "record_stock_movement", "transfer_stock", "receive_against_po", "record_unexpected_receipt",
    "raise_stock_request", "create_purchase_order", "update_po_expected_date", "cancel_po",
    "clear_payment_tranche", "set_reorder_policy", "decide_approval", "create_user",
    "deactivate_user", "create_vendor",
}  # fmt: skip


def bucket(case: EvalCase) -> str:
    return case.id.split("-")[0]


def test_ids_are_unique() -> None:
    ids = [c.id for c in CORPUS]
    assert len(ids) == len(set(ids))


def test_coverage_targets() -> None:
    by_bucket = Counter(bucket(c) for c in CORPUS)
    minimums = {"RD": 30, "CL": 8, "RB": 12, "OS": 4, "NP": 4, "VZ": 4, "WR": 6, "HL": 3}
    assert {b: n for b, n in minimums.items() if by_bucket[b] < n} == {}
    reads_per_role = Counter(c.role for c in CORPUS if bucket(c) == "RD")
    assert min(reads_per_role.values()) >= 10
    assert sum(c.lang != "en" for c in CORPUS) >= 15
    assert {c.role for c in CORPUS if c.expected_route == "refuse_rbac"} == {
        "inventory_manager",
        "procurement_manager",
    }


def test_refusals_expect_the_fixed_denial_sentence() -> None:
    for case in CORPUS:
        if case.expected_route == "refuse_rbac" or "mixed" in case.tags:
            assert DENIAL[case.lang] in case.must_include, case.id


def test_write_cases_name_known_tools() -> None:
    tools = {c.write_expect.tool for c in CORPUS if c.write_expect}
    assert tools <= KNOWN_TOOLS


def test_answer_keys_exist_for_exactly_the_sql_cases() -> None:
    assert set(KEYS) == {c.id for c in CORPUS if c.answer_key_sql}


@pytest.mark.parametrize("case", [c for c in CORPUS if c.answer_key_sql], ids=lambda c: c.id)
def test_frozen_answer_keys_match_database(db: object, case: EvalCase) -> None:
    """Rebuild each key as its role and compare. Fails if seeds/views drift from the keys:
    rerun `make answer-keys`, review the diff and commit it with the change."""
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    assert compute_key(case) == KEYS[case.id]
