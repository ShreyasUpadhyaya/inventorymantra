"""Prompt text in code must equal the approved text in docs/PROMPTS.md (CLAUDE.md, gate G1).

Changing either side alone fails here: edit PROMPTS.md, bump PROMPT_VERSION, log the change
in PROMPTS.md §12 and get approval, then update the code to match.
"""

import re
from pathlib import Path

from app.agent.prompts import CORE_SYSTEM_PROMPT, GUARD_TASK, PROMPT_VERSION, ROLE_CAPABILITIES
from app.agent.prompts.core import DENIAL, OUT_OF_SCOPE
from app.agent.prompts.help import EXAMPLES

DOC = (Path(__file__).resolve().parents[3] / "docs" / "PROMPTS.md").read_text(encoding="utf-8")


def section(start: str, end: str) -> str:
    match = re.search(rf"^{re.escape(start)}.*?(?=^{re.escape(end)})", DOC, re.M | re.S)
    assert match, f"section {start!r} not found in PROMPTS.md"
    return match.group(0)


def text_blocks(chunk: str) -> list[str]:
    return re.findall(r"```text\n(.*?)\n```", chunk, re.S)


def test_prompt_version_matches_doc() -> None:
    assert f"**v{PROMPT_VERSION}, APPROVED" in DOC
    assert re.search(rf"^\| {re.escape(PROMPT_VERSION)} \|", DOC, re.M)


def test_core_system_prompt_is_verbatim() -> None:
    blocks = text_blocks(section("## 1. CORE SYSTEM PROMPT", "### 1a."))
    assert blocks == [CORE_SYSTEM_PROMPT]


def test_role_capability_blocks_are_verbatim() -> None:
    blocks = text_blocks(section("### 1a.", "## 2. Call: GUARD"))
    assert blocks == [
        ROLE_CAPABILITIES["inventory_manager"],
        ROLE_CAPABILITIES["procurement_manager"],
        ROLE_CAPABILITIES["owner"],
    ]


def test_guard_task_is_verbatim() -> None:
    blocks = text_blocks(section("## 2. Call: GUARD", "## 3. Call: CLARIFY"))
    assert blocks == [GUARD_TASK]


def test_fixed_sentences_match_the_core_prompt() -> None:
    for lang in ("en", "hi", "hinglish"):
        assert f'{lang} "{DENIAL[lang]}"' in CORE_SYSTEM_PROMPT  # type: ignore[index]
        assert f'{lang} "{OUT_OF_SCOPE[lang]}"' in CORE_SYSTEM_PROMPT  # type: ignore[index]


def test_help_examples_match_doc() -> None:
    table = section("## 9. Help mode", "## 10.")
    labels = {
        "inventory_manager": "Inventory Manager",
        "procurement_manager": "Procurement Manager",
        "owner": "Owner",
    }
    for role, label in labels.items():
        row = next(line for line in table.splitlines() if line.startswith(f"| **{label}**"))
        assert re.findall(r'\d\. "([^"]+)"', row) == EXAMPLES[role]


def test_sql_task_is_verbatim() -> None:
    from app.agent.prompts.sql import SQL_TASK

    blocks = text_blocks(section("## 4. Call: SQL GENERATION", "## 5. Call: ANSWER COMPOSER"))
    assert blocks == [SQL_TASK]


def test_clarify_task_is_verbatim() -> None:
    from app.agent.prompts.clarify import CLARIFY_TASK

    blocks = text_blocks(section("## 3. Call: CLARIFY", "## 4. Call: SQL GENERATION"))
    assert blocks == [CLARIFY_TASK]


def test_composer_viz_and_judge_are_verbatim() -> None:
    from app.agent.prompts.compose import COMPOSE_TASK, JUDGE_SYSTEM, VIZ_TASK

    assert text_blocks(section("## 5. Call: ANSWER COMPOSER", "## 6. Call: VIZ")) == [COMPOSE_TASK]
    assert text_blocks(section("## 6. Call: VIZ", "## 7. Call: WRITE")) == [VIZ_TASK]
    assert text_blocks(section("## 8. Call: NO-PREDICTION JUDGE", "## 9. Help mode")) == [
        JUDGE_SYSTEM
    ]
