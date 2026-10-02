"""The offline stand-in routes the corpus's fixed-reply cases correctly, so the offline demo
(LLM_MODE=fake) behaves sensibly for refusals and help. This measures the stand-in only;
real routing quality is measured in openai mode at C7.1."""

import pytest

from app.agent.run import run_turn
from evals.schema import EvalCase, load_corpus
from tests.agent.conftest import make_user

FIXED = [c for c in load_corpus() if c.expected_route in ("refuse_scope", "refuse_rbac", "help")]


@pytest.mark.parametrize("case", FIXED, ids=lambda c: c.id)
def test_fake_router_on_fixed_reply_cases(case: EvalCase) -> None:
    result = run_turn(make_user(case.role), case.query)
    assert result.route == case.expected_route
    assert result.lang == case.lang
    for text in case.must_include:
        assert text in result.answer
    for text in case.must_not_contain:
        assert text not in result.answer
