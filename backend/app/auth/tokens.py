"""Access JWTs (short-lived, stateless) and refresh tokens (random, stored hashed)."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from pydantic import BaseModel, ConfigDict

from app.core.config import get_settings

ACCESS_COOKIE = "im_access"
REFRESH_COOKIE = "im_refresh"
ISSUER = "inventorymantra"
AUDIENCE = "inventorymantra-web"
ALGORITHM = "HS256"


class InvalidTokenError(Exception):
    pass


class AccessClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    sub: UUID
    ver: int


def create_access_token(user_id: UUID, token_version: int, now: datetime | None = None) -> str:
    """The role is deliberately NOT in the token: it is read from the DB on every request."""
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "ver": token_version,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": issued,
        "exp": issued + timedelta(minutes=get_settings().access_token_minutes),
    }
    return jwt.encode(claims, get_settings().jwt_secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        return AccessClaims.model_validate(claims)
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError from exc


def new_refresh_token() -> tuple[str, str]:
    """Return (token for the cookie, SHA-256 hash for the DB). Raw tokens are never stored."""
    token = secrets.token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
