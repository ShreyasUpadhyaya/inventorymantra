"""Golden-corpus case schema (docs/QUALITY_AND_EVALS.md §1)."""

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_PATH = REPO_ROOT / "evals" / "corpus" / "golden.jsonl"
ANSWER_KEYS_PATH = REPO_ROOT / "evals" / "corpus" / "answer_keys.json"

Role = Literal["inventory_manager", "procurement_manager", "owner"]
Lang = Literal["en", "hi", "hinglish"]
Route = Literal["read", "clarify", "write_preview", "refuse_scope", "refuse_rbac", "help"]
VizType = Literal["none", "kpi", "table", "bar", "line", "pie", "stacked_bar", "flow"]
Compare = Literal["exact", "set", "set_numeric_tol", "scalar_tol", "contains", "none"]

DENIAL = {
    "en": "That information isn't available for your role.",
    "hi": "यह जानकारी आपकी भूमिका के लिए उपलब्ध नहीं है।",
    "hinglish": "Yeh jaankari aapke role ke liye available nahi hai.",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Turn(Strict):
    role: Literal["user", "assistant"]
    content: str


class ClarifyExpect(Strict):
    dimension: Literal["time_window", "entity", "measure"]
    min_options: int = Field(default=2, ge=2, le=4)


class WriteExpect(Strict):
    tool: str
    args: dict[str, str | int | float] = {}
    approval_required: bool = False
    expect_error: str | None = None  # e.g. "insufficient_stock": nothing may be written


class EvalCase(Strict):
    id: str = Field(pattern=r"^[A-Z]{2}-[A-Z]{1,2}-\d{2}$")
    role: Role
    lang: Lang
    query: str = Field(min_length=1)
    history: list[Turn] = []
    expected_route: Route
    answer_key_sql: str | None = None
    compare: Compare = "none"
    key_columns: list[str] = []  # columns compared for set modes (all if empty)
    expect_empty: bool = False  # the correct answer is "no matching records"
    expected_viz: list[VizType] = []
    must_include: list[str] = []
    must_not_contain: list[str] = []
    clarify_expect: ClarifyExpect | None = None
    write_expect: WriteExpect | None = None
    tags: list[str] = []

    @model_validator(mode="after")
    def _route_requirements(self) -> Self:
        route = self.expected_route
        if route == "read" and (not self.answer_key_sql or self.compare == "none"):
            raise ValueError(f"{self.id}: read cases need answer_key_sql and a compare mode")
        if route in ("refuse_scope", "refuse_rbac", "help", "clarify") and self.answer_key_sql:
            raise ValueError(f"{self.id}: {route} cases must not have answer_key_sql")
        if route == "refuse_rbac" and not self.must_not_contain:
            raise ValueError(f"{self.id}: refusal cases must list leak strings")
        if route == "clarify" and self.clarify_expect is None:
            raise ValueError(f"{self.id}: clarify cases need clarify_expect")
        if route == "write_preview" and self.write_expect is None:
            raise ValueError(f"{self.id}: write cases need write_expect")
        if self.expect_empty and route != "read":
            raise ValueError(f"{self.id}: expect_empty only applies to read cases")
        return self


def load_corpus(path: Path = CORPUS_PATH) -> list[EvalCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [EvalCase.model_validate_json(line) for line in lines if line.strip()]


def load_answer_keys(path: Path = ANSWER_KEYS_PATH) -> dict[str, dict[str, object]]:
    data: dict[str, dict[str, object]] = json.loads(path.read_text(encoding="utf-8"))
    return data
