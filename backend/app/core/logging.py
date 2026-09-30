"""Logging with a redaction filter so secrets and tokens never reach log output."""

import logging
import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),  # OpenAI keys
    re.compile(r"lsv2_[A-Za-z0-9_\-]{10,}"),  # LangSmith keys
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWTs
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+(@)"),  # DSN passwords
    re.compile(r"(?i)((?:password|secret|api_key|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
]
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    for pattern in _PATTERNS:
        if pattern.groups == 2:
            text = pattern.sub(rf"\g<1>{REDACTED}\g<2>", text)
        elif pattern.groups == 1:
            text = pattern.sub(rf"\g<1>{REDACTED}", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
