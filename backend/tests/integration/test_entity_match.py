"""Entity matcher contract (DECISION D-026). Implementation: app/agent/entity_match.py (C4.2).

resolve(conn, role, kind, text) -> result with:
  outcome: "match" | "clarify" | "not_found" | "denied"
  candidates: list of objects with `.code` (item_code / vendor name / location name /
              po_number) and `.name`; the match first, clarify options (2-4) or
              not_found suggestions (0-3).
`text` is one entity mention copied as typed (PROMPTS.md v0.3), never the whole sentence.
Expectations come from the scratch prototype run against the seeded data (D-026).
"""

from typing import Any

import psycopg
import pytest

IM, PM, OW = "inventory_manager", "procurement_manager", "owner"


def resolve(conn: psycopg.Connection, role: str, kind: str, text: str) -> Any:
    from app.agent.entity_match import resolve as _resolve

    return _resolve(conn, role=role, kind=kind, text=text)


# ------------------------------------------------------------------ items: confident match
@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("ms bush od40", "BO-MC-0100"),
        ("MS Bush OD40xID26x70", "BO-MC-0100"),  # x for ×, typo ID26 for ID26.5
        ("ms bush 40", "BO-MC-0100"),  # bare number matches the OD40 atom only
        ("bush OD40", "BO-MC-0100"),
        ("hex nut m3", "BO-MC-NT-0011"),  # m3 eliminates M8 / M10
        ("torx screw m3", "BO-MC-HX-0003"),  # catalogue synonym torx -> hexalobular
        ("AC contacter", "BO-EL-0144"),  # typo
        ("ac contactor schneider", "BO-EL-0144"),
        ("BO-EL-0144", "BO-EL-0144"),  # item code
        ("bo-el-0144", "BO-EL-0144"),
        ("ss sheet 304 1500x3000x2", "RM-MT-FS-0075"),  # 2mm, not 4mm; 304, not 316
        ("boom barrier motor", "NW-33837"),  # word order differs from the stored name
        ("relay base", "BO-EL-0140"),
    ],
)
def test_item_match(db: psycopg.Connection, text: str, code: str) -> None:
    result = resolve(db, IM, "item", text)
    assert result.outcome == "match"
    assert result.candidates[0].code == code


# ------------------------------------------------------------------ items: ask back
@pytest.mark.parametrize(
    ("text", "must_offer", "max_options"),
    [
        ("ms bush", {"BO-MC-0100", "BO-MC-0101"}, 2),  # no "MS Beam" / "Push Button" noise
        ("hex nut", {"BO-MC-NT-0011"}, 4),
        ("ss sheet", set(), 4),
        ("gear box", set(), 4),
    ],
)
def test_item_clarify(
    db: psycopg.Connection, text: str, must_offer: set[str], max_options: int
) -> None:
    result = resolve(db, IM, "item", text)
    assert result.outcome == "clarify"
    codes = {c.code for c in result.candidates}
    assert 2 <= len(codes) <= max_options
    assert must_offer <= codes


# ------------------------------------------------------------------ items: nothing suitable
def test_size_conflict_is_not_found_with_suggestion(db: psycopg.Connection) -> None:
    result = resolve(db, IM, "item", "torx screw m4")  # only an M3 hexalobular exists
    assert result.outcome == "not_found"
    assert "BO-MC-HX-0003" in {c.code for c in result.candidates}
    assert len(result.candidates) <= 3


@pytest.mark.parametrize("text", ["titanium flange", "unobtainium widget 9000", "zzzz"])
def test_no_match(db: psycopg.Connection, text: str) -> None:
    result = resolve(db, IM, "item", text)
    assert result.outcome == "not_found"
    assert len(result.candidates) <= 3


@pytest.mark.parametrize("text", ["'; DROP TABLE inv_items; --", "%", "_", "\\"])
def test_mentions_are_data_not_sql(db: psycopg.Connection, text: str) -> None:
    result = resolve(db, IM, "item", text)
    assert result.outcome in {"not_found", "clarify"}
    row = db.execute("SELECT count(*) FROM inv_items").fetchone()
    assert row == (501,)


# ------------------------------------------------------------------ vendors, locations, POs
def test_vendor_typo_matches(db: psycopg.Connection) -> None:
    result = resolve(db, PM, "vendor", "sterling steel")
    assert result.outcome == "match"
    assert result.candidates[0].code == "Sterling Steels Pvt. Ltd."


def test_ambiguous_vendor_asks(db: psycopg.Connection) -> None:
    result = resolve(db, PM, "vendor", "Shakti")
    assert result.outcome == "clarify"
    assert {c.code for c in result.candidates} >= {
        "Shakti Steels Pvt. Ltd.",
        "Shakti Engineering Pvt. Ltd.",
    }


def test_vendor_lookup_is_denied_for_inventory_manager(db: psycopg.Connection) -> None:
    """Denied, not "not found": a not-found answer would leak whether the vendor exists."""
    for text in ("sterling steel", "no such vendor ltd"):
        result = resolve(db, IM, "vendor", text)
        assert result.outcome == "denied"
        assert result.candidates == []


@pytest.mark.parametrize(
    ("text", "code"), [("132-2", "132-2"), ("godown 132-2", "132-2"), ("C84", "C-84")]
)
def test_location_match(db: psycopg.Connection, text: str, code: str) -> None:
    result = resolve(db, IM, "location", text)
    assert result.outcome == "match"
    assert result.candidates[0].code == code


@pytest.mark.parametrize("text", ["PO 3", "po-0003", "PO-2026-0003", "po 2026 3"])
def test_po_number_normalisation(db: psycopg.Connection, text: str) -> None:
    result = resolve(db, PM, "po", text)
    assert result.outcome == "match"
    assert result.candidates[0].code == "PO-2026-0003"


def test_unknown_po_is_not_found(db: psycopg.Connection) -> None:
    assert resolve(db, PM, "po", "PO 999").outcome == "not_found"
