"""Try the agent from a terminal (manual testing before the chat API/UI exist).

    uv run python -m app.agent.cli --role inventory_manager "What price did we pay for SS sheet?"
    uv run python -m app.agent.cli --role owner --file question.txt   # Hindi: UTF-8 file

Uses LLM_MODE from .env (fake by default: no OpenAI calls). Runs as a synthetic demo user of
the given role; nothing is written to the database.

Windows note: GNU make passes arguments through the ANSI code page, which turns Devanagari
into '?'. Run this module directly from PowerShell, or put the question in a UTF-8 file.
"""

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

from app.agent.run import run_turn
from app.agent.tracing import configure_tracing
from app.auth.service import AuthUser
from app.core.config import get_settings

MANGLED_HELP = (
    "The question arrived as question marks: your terminal or `make` dropped non-English\n"
    "characters before they reached the app. Run the CLI directly from PowerShell, or save the\n"
    "question in a UTF-8 file and use --file (make ask ROLE=owner FILE=question.txt)."
)


def looks_mangled(text: str) -> bool:
    """True when a code-page conversion replaced the letters with '?' (e.g. Hindi via make)."""
    stripped = re.sub(r"\s+", "", text)
    return len(stripped) >= 3 and stripped.count("?") / len(stripped) > 0.5


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("message", nargs="?", help="the question (omit when using --file)")
    parser.add_argument("--file", type=Path, help="read the question from a UTF-8 text file")
    parser.add_argument(
        "--role",
        choices=["inventory_manager", "procurement_manager", "owner"],
        default="inventory_manager",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Hindi on Windows consoles
    if args.file:
        message = args.file.read_text(encoding="utf-8-sig").strip()
    elif args.message:
        message = args.message
    else:
        parser.error("give a question or --file")
    if looks_mangled(message):
        sys.exit(MANGLED_HELP)
    traced = configure_tracing()
    user = AuthUser(
        id=uuid.uuid4(), email="cli@local", full_name="CLI", role=args.role,
        is_active=True, token_version=0,
    )  # fmt: skip
    result = run_turn(user, message)
    print(f"[llm_mode={get_settings().llm_mode} tracing={traced}]")
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
