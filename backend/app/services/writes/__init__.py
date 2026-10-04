"""Typed write actions (docs/WRITE_PATH.md). Importing this package registers every action."""

from app.services.writes import stock  # noqa: F401 (registers the stock actions)
from app.services.writes.framework import (
    REGISTRY,
    Preview,
    WriteError,
    WriteResult,
    apply,
    cancel,
    preview,
)

__all__ = ["REGISTRY", "Preview", "WriteError", "WriteResult", "apply", "cancel", "preview"]
