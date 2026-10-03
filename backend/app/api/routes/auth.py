"""Login, token refresh, logout and the current-user endpoint (DECISION D-014)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import UNAUTHORIZED, Conn, CurrentUser
from app.auth.rate_limit import login_limiter
from app.auth.service import (
    AuthUser,
    RefreshReuseError,
    Role,
    authenticate,
    issue_tokens,
    normalise_email,
    revoke_refresh,
    rotate_refresh,
)
from app.auth.tokens import ACCESS_COOKIE, REFRESH_COOKIE
from app.core.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])
API_PREFIX = "/api"  # every route is also served here for the web app (app/main.py)

INVALID_LOGIN = "Invalid email or password."


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: Role

    @classmethod
    def of(cls, user: AuthUser) -> "UserOut":
        return cls(id=user.id, email=user.email, full_name=user.full_name, role=user.role)


def _refresh_path(request: Request) -> str:
    """/auth, or /api/auth when called through the web app's same-origin proxy (C5.1)."""
    return f"{API_PREFIX}/auth" if request.url.path.startswith(f"{API_PREFIX}/") else "/auth"


def _set_session_cookies(request: Request, response: Response, access: str, refresh: str) -> None:
    settings = get_settings()
    for name, value, max_age, path in (
        (ACCESS_COOKIE, access, settings.access_token_minutes * 60, "/"),
        # the refresh token is only ever sent to the auth endpoints
        (REFRESH_COOKIE, refresh, settings.refresh_token_days * 86_400, _refresh_path(request)),
    ):
        response.set_cookie(
            name,
            value,
            max_age=max_age,
            path=path,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )


def _clear_session_cookies(request: Request, response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path=_refresh_path(request))


AUTH_NOT_CONFIGURED = HTTPException(
    status.HTTP_503_SERVICE_UNAVAILABLE,
    "Login is not configured on the server (JWT_SECRET_KEY missing or shorter than 32 chars).",
)


def require_auth_configured() -> None:
    try:
        get_settings().jwt_secret()
    except RuntimeError:
        raise AUTH_NOT_CONFIGURED from None


@router.post("/login", response_model=UserOut)
def login(
    body: Annotated[LoginIn, Body()], request: Request, response: Response, conn: Conn
) -> UserOut:
    require_auth_configured()
    email = normalise_email(body.email)
    client_ip = request.client.host if request.client else "unknown"
    if login_limiter.is_blocked(email, client_ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later."
        )
    user = authenticate(conn, email, body.password)
    if user is None:
        login_limiter.record_failure(email, client_ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)
    login_limiter.reset(email, client_ip)
    access, refresh = issue_tokens(conn, user)
    _set_session_cookies(request, response, access, refresh)
    return UserOut.of(user)


@router.post("/refresh", response_model=UserOut)
def refresh(request: Request, response: Response, conn: Conn) -> UserOut:
    require_auth_configured()
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise UNAUTHORIZED
    try:
        rotated = rotate_refresh(conn, token)
    except RefreshReuseError:
        conn.commit()  # keep the "revoke every session" write despite the 401 below
        raise UNAUTHORIZED from None
    if rotated is None:
        raise UNAUTHORIZED
    user, access, new_refresh = rotated
    _set_session_cookies(request, response, access, new_refresh)
    return UserOut.of(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, conn: Conn) -> None:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        revoke_refresh(conn, token)
    _clear_session_cookies(request, response)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.of(user)
