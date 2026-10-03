"""Convert DB values into JSON-friendly ones for API responses and agent state."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value
