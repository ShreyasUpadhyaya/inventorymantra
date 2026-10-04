"""Typed write actions (docs/WRITE_PATH.md). Importing this package registers every action."""

from app.services.writes import admin, procurement, stock  # noqa: F401 (registers the actions)
from app.services.writes.framework import (
    REGISTRY,
    Preview,
    WriteError,
    WriteResult,
    apply,
    apply_batch,
    cancel,
    preview,
)

__all__ = [
    "REGISTRY", "Preview", "WriteError", "WriteResult", "apply", "apply_batch", "cancel",
    "preview",
]  # fmt: skip
