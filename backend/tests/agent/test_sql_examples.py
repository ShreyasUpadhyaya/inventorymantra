"""Few-shot SQL examples: valid for their role, runnable, and not copied from the corpus."""

import re
from datetime import date

import psycopg
import pytest

from app.agent.prompts.sql_examples import EXAMPLES, Example
from app.agent.schema_context import allowed_views
from app.agent.sql_guard import validate_sql
from app.core.config import get_settings
from app.db.readonly import readonly_session
from evals.schema import load_corpus

CASES = [(role, ex) for role, examples in EXAMPLES.items() for ex in examples if ex.sql]


def plain(text: str) -> str:
    text = re.sub(r"<data>.*?</data>|\(today is[^)]*\)", "", text, flags=re.S)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def test_no_example_repeats_a_corpus_question() -> None:
    corpus = {plain(c.query) for c in load_corpus()}
    corpus_sql = {
        re.sub(r"\s+", " ", c.answer_key_sql).lower() for c in load_corpus() if c.answer_key_sql
    }
    for examples in EXAMPLES.values():
        for ex in examples:
            assert plain(ex.question) not in corpus, ex.question
            if ex.sql:
                assert re.sub(r"\s+", " ", ex.sql).lower() not in corpus_sql, ex.question


@pytest.mark.parametrize(("role", "example"), CASES, ids=lambda v: str(v)[:40])
def test_example_passes_the_guard_and_runs_as_its_role(
    db: psycopg.Connection, role: str, example: Example
) -> None:
    if get_settings().readonly_db_password is None:
        pytest.skip("READONLY_DB_PASSWORD not set")
    result = validate_sql(example.sql or "", allowed_views(role))  # type: ignore[arg-type]
    assert result.ok, result.reason
    with readonly_session(role, None, date(2026, 9, 30)) as conn:  # type: ignore[arg-type]
        conn.execute(result.sql or "")


def test_each_role_has_five_to_ten_examples() -> None:
    for role, examples in EXAMPLES.items():
        assert 5 <= len(examples) <= 10, role
