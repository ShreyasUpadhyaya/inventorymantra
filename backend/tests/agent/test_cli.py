"""The terminal tool refuses text that a code page turned into question marks."""

import pytest

from app.agent.cli import looks_mangled


@pytest.mark.parametrize("text", ["??? ???? ??? ???? ????", "????", "?? ?? ??"])
def test_mangled_input_is_detected(text: str) -> None:
    assert looks_mangled(text)


@pytest.mark.parametrize(
    "text",
    ["मैं आपसे क्या पूछ सकता हूँ?", "Which POs are overdue?", "kitne hain??", "??", "What? Why? How?"],
)
def test_real_questions_pass(text: str) -> None:
    assert not looks_mangled(text)
