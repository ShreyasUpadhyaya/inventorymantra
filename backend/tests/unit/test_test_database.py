"""Tests and evals use the "_test" database, never the demo one (C7.0, D-051)."""

from collections.abc import Iterator

import pytest

from app.core import config
from app.db.session import database_url_for_tests, psycopg_dsn, use_test_database

DEMO = "postgresql+psycopg://u:p@127.0.0.1:5433/inventorymantra"


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    config.get_settings.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()


def test_this_test_run_is_on_the_test_database() -> None:
    assert psycopg_dsn().rsplit("/", 1)[1].split("?")[0].endswith("_test")


def test_the_test_database_is_the_demo_name_plus_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DEMO)
    assert database_url_for_tests() == DEMO + "_test"


def test_switching_twice_does_not_add_test_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DEMO)
    assert use_test_database() == "inventorymantra_test"
    assert use_test_database() == "inventorymantra_test"


def test_an_explicit_url_must_name_a_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DEMO)
    monkeypatch.setenv("TEST_DATABASE_URL", DEMO)
    with pytest.raises(RuntimeError, match="_test"):
        database_url_for_tests()
    monkeypatch.setenv("TEST_DATABASE_URL", DEMO.replace("inventorymantra", "other_test"))
    config.get_settings.cache_clear()
    assert database_url_for_tests().endswith("/other_test")
