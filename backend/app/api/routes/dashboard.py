"""GET /dashboard: the signed-in user's role dashboard (C3.3)."""

import psycopg
from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser
from app.core.config import app_today
from app.services.dashboard import DashboardOut, build

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(user: CurrentUser) -> DashboardOut:
    try:
        return build(user.role, user.id, app_today())
    except (RuntimeError, psycopg.OperationalError):  # role users not configured / unreachable
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Dashboards are unavailable: the read-only database users are not configured.",
        ) from None
