"""Shared FastAPI dependencies. `current_user` is the single gate for authenticated routes."""

from collections.abc import Iterator
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, Request, status

from app.auth.service import AuthUser, load_user
from app.auth.tokens import ACCESS_COOKIE, InvalidTokenError, decode_access_token
from app.db.pool import get_pool


def get_conn() -> Iterator[psycopg.Connection]:
    """A pooled connection; commits if the request succeeds, rolls back on error."""
    with get_pool().connection() as conn:
        yield conn


Conn = Annotated[psycopg.Connection, Depends(get_conn)]

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _token_from(request: Request) -> str | None:
    cookie = request.cookies.get(ACCESS_COOKIE)
    if cookie:
        return cookie
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


def current_user(request: Request, conn: Conn) -> AuthUser:
    token = _token_from(request)
    if token is None:
        raise UNAUTHORIZED
    try:
        claims = decode_access_token(token)
    except InvalidTokenError:
        raise UNAUTHORIZED from None
    except RuntimeError:  # JWT_SECRET_KEY missing/too short: a server problem, not the user's
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Login is not configured on the server."
        ) from None
    user = load_user(conn, claims.sub)  # role and status always come from the DB
    if user is None or not user.is_active or user.token_version != claims.ver:
        raise UNAUTHORIZED
    return user


CurrentUser = Annotated[AuthUser, Depends(current_user)]
