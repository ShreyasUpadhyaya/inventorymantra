"""Login, session and token behaviour against the real seeded database (C3.1)."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.rate_limit import login_limiter
from app.auth.tokens import ACCESS_COOKIE, REFRESH_COOKIE, create_access_token
from app.core.config import get_settings
from app.main import app

PASSWORD = "correct horse battery staple"  # noqa: S105 (test-only user)
USERS = {
    "procurement_manager": ("pm@test.inventorymantra", True),
    "inventory_manager": ("im@test.inventorymantra", True),
    "owner": ("inactive@test.inventorymantra", False),
}


@pytest.fixture(scope="module", autouse=True)
def _requires_jwt_secret() -> None:
    try:
        get_settings().jwt_secret()
    except RuntimeError:
        pytest.skip("JWT_SECRET_KEY (>= 32 chars) not set")


@pytest.fixture
def users(db: psycopg.Connection) -> Iterator[dict[str, uuid.UUID]]:
    """Throwaway users with a known password; removed (with their tokens) afterwards."""
    password_hash = hash_password(PASSWORD)
    ids: dict[str, uuid.UUID] = {}
    for role, (email, active) in USERS.items():
        user_id = uuid.uuid4()
        db.execute(
            """INSERT INTO users (id, email, password_hash, full_name, role, is_active)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, email, password_hash, f"Test {role}", role, active),
        )
        ids[email] = user_id
    login_limiter.clear()
    yield ids
    login_limiter.clear()
    db.execute("DELETE FROM refresh_tokens WHERE user_id = ANY(%s)", (list(ids.values()),))
    db.execute("DELETE FROM users WHERE id = ANY(%s)", (list(ids.values()),))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def client_with(cookie: str, value: str) -> TestClient:
    """A fresh client carrying one cookie, e.g. a forged token or a stolen refresh token."""
    c = TestClient(app)
    c.cookies.set(cookie, value)
    return c


def login(client: TestClient, email: str, password: str = PASSWORD) -> object:
    return client.post("/auth/login", json={"email": email, "password": password})


PM_EMAIL = USERS["procurement_manager"][0]


# ------------------------------------------------------------------ login
@pytest.mark.usefixtures("users")
def test_login_sets_httponly_cookies_and_returns_safe_profile(client: TestClient) -> None:
    r = login(client, "  PM@Test.InventoryMantra ")  # trimmed + case-insensitive
    assert r.status_code == 200
    assert r.json() == {
        "id": r.json()["id"],
        "email": PM_EMAIL,
        "full_name": "Test procurement_manager",
        "role": "procurement_manager",
        "must_change_password": False,
    }
    cookies = r.headers.get_list("set-cookie")
    access = next(c for c in cookies if c.startswith(f"{ACCESS_COOKIE}="))
    refresh = next(c for c in cookies if c.startswith(f"{REFRESH_COOKIE}="))
    for cookie in (access, refresh):
        assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Path=/auth" in refresh
    assert "password_hash" not in r.text and "$2b$" not in r.text


@pytest.mark.usefixtures("users")
def test_web_app_prefix_serves_the_same_session_flow(client: TestClient) -> None:
    """C5.1: the web app proxies /api/* to the API (same origin), so the refresh cookie must be
    scoped to /api/auth there, and refresh/me/logout must work through that prefix."""
    r = client.post("/api/auth/login", json={"email": PM_EMAIL, "password": PASSWORD})
    assert r.status_code == 200
    refresh = next(c for c in r.headers.get_list("set-cookie") if c.startswith(REFRESH_COOKIE))
    assert "Path=/api/auth" in refresh
    assert client.get("/api/auth/me").json()["email"] == PM_EMAIL
    assert client.post("/api/auth/refresh").status_code == 200
    assert client.get("/api/dashboard").status_code in (200, 503)
    assert client.post("/api/auth/logout").status_code == 204
    assert "/api/auth/login" not in client.get("/openapi.json").json()["paths"]  # docs list once


@pytest.mark.usefixtures("users")
@pytest.mark.parametrize(
    ("email", "password"),
    [
        (PM_EMAIL, "wrong password"),
        ("nobody@test.inventorymantra", PASSWORD),
        ("inactive@test.inventorymantra", PASSWORD),
    ],
)
def test_bad_logins_get_one_identical_answer(client: TestClient, email: str, password: str) -> None:
    r = login(client, email, password)
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid email or password."}
    assert not r.headers.get_list("set-cookie")


@pytest.mark.usefixtures("users")
def test_login_rejects_form_posts_and_extra_fields(client: TestClient) -> None:
    form = client.post("/auth/login", data={"email": PM_EMAIL, "password": PASSWORD})
    assert form.status_code == 422  # JSON only: a cross-site HTML form can't log in
    extra = client.post(
        "/auth/login", json={"email": PM_EMAIL, "password": PASSWORD, "role": "owner"}
    )
    assert extra.status_code == 422


@pytest.mark.usefixtures("users")
def test_repeated_failures_are_rate_limited(client: TestClient) -> None:
    for _ in range(get_settings().login_max_failures):
        assert login(client, PM_EMAIL, "nope").status_code == 401
    blocked = login(client, PM_EMAIL)  # even the right password is refused while blocked
    assert blocked.status_code == 429


# ------------------------------------------------------------------ /me and access tokens
@pytest.mark.usefixtures("users")
def test_me_requires_a_valid_session(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    login(client, PM_EMAIL)
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "procurement_manager"


def test_bearer_header_also_works(client: TestClient, users: dict[str, uuid.UUID]) -> None:
    token = create_access_token(users[PM_EMAIL], token_version=0)
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def forged(user_id: uuid.UUID, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(user_id), "ver": 0, "iss": "inventorymantra", "aud": "inventorymantra-web",
        "iat": now, "exp": now + timedelta(minutes=5),
    }  # fmt: skip
    key = str(overrides.pop("key", get_settings().jwt_secret()))
    algorithm = str(overrides.pop("alg", "HS256"))
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm=algorithm)


@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},  # expired
        {"key": "x" * 40},  # signed with someone else's key
        {"aud": "another-app"},
        {"iss": "evil"},
        {"ver": 7},  # stale token_version
        {"sub": str(uuid.uuid4())},  # no such user
    ],
    ids=["expired", "wrong-key", "wrong-audience", "wrong-issuer", "stale-version", "no-user"],
)
def test_bad_access_tokens_are_rejected(
    client: TestClient, users: dict[str, uuid.UUID], overrides: dict[str, object]
) -> None:
    token = forged(users[PM_EMAIL], **overrides)
    assert client_with(ACCESS_COOKIE, token).get("/auth/me").status_code == 401


def test_unsigned_and_garbage_tokens_are_rejected(
    client: TestClient, users: dict[str, uuid.UUID]
) -> None:
    unsigned = jwt.encode({"sub": str(users[PM_EMAIL]), "ver": 0}, key=None, algorithm="none")
    for token in (unsigned, "not-a-jwt", ""):
        assert client_with(ACCESS_COOKIE, token).get("/auth/me").status_code == 401


def test_role_and_status_come_from_the_database_on_every_request(
    client: TestClient, users: dict[str, uuid.UUID], db: psycopg.Connection
) -> None:
    login(client, PM_EMAIL)
    db.execute("UPDATE users SET role = 'inventory_manager' WHERE id = %s", (users[PM_EMAIL],))
    assert client.get("/auth/me").json()["role"] == "inventory_manager"  # demotion is instant
    db.execute("UPDATE users SET is_active = false WHERE id = %s", (users[PM_EMAIL],))
    assert client.get("/auth/me").status_code == 401  # deactivation is instant


def test_token_version_bump_logs_out_everywhere(
    client: TestClient, users: dict[str, uuid.UUID], db: psycopg.Connection
) -> None:
    login(client, PM_EMAIL)
    db.execute(
        "UPDATE users SET token_version = token_version + 1 WHERE id = %s", (users[PM_EMAIL],)
    )
    assert client.get("/auth/me").status_code == 401


# ------------------------------------------------------------------ refresh and logout
@pytest.mark.usefixtures("users")
def test_refresh_rotates_the_token(client: TestClient) -> None:
    login(client, PM_EMAIL)
    first = client.cookies.get(REFRESH_COOKIE)
    r = client.post("/auth/refresh")
    assert r.status_code == 200 and r.json()["email"] == PM_EMAIL
    assert client.cookies.get(REFRESH_COOKIE) not in (None, first)


def test_reusing_a_rotated_refresh_token_revokes_all_sessions(
    client: TestClient, users: dict[str, uuid.UUID], db: psycopg.Connection
) -> None:
    login(client, PM_EMAIL)
    stolen = client.cookies.get(REFRESH_COOKIE)
    assert client.post("/auth/refresh").status_code == 200  # legitimate rotation
    replay = client_with(REFRESH_COOKIE, stolen or "").post("/auth/refresh")
    assert replay.status_code == 401
    active = db.execute(
        "SELECT count(*) FROM refresh_tokens WHERE user_id = %s AND revoked_at IS NULL",
        (users[PM_EMAIL],),
    ).fetchone()
    assert active == (0,)  # the victim's current session is cut off too
    assert client.get("/auth/me").status_code == 401  # access token invalidated (token_version)


def test_logout_revokes_and_clears(
    client: TestClient, users: dict[str, uuid.UUID], db: psycopg.Connection
) -> None:
    login(client, PM_EMAIL)
    refresh_token = client.cookies.get(REFRESH_COOKIE)
    assert client.post("/auth/logout").status_code == 204
    assert client_with(REFRESH_COOKIE, refresh_token or "").post("/auth/refresh").status_code == 401
    row = db.execute(
        "SELECT count(*) FROM refresh_tokens WHERE user_id = %s AND revoked_at IS NULL",
        (users[PM_EMAIL],),
    ).fetchone()
    assert row == (0,)


def test_refresh_without_cookie_is_rejected(client: TestClient) -> None:
    assert client.post("/auth/refresh").status_code == 401


def test_refresh_tokens_are_stored_hashed(
    client: TestClient, users: dict[str, uuid.UUID], db: psycopg.Connection
) -> None:
    login(client, PM_EMAIL)
    raw = client.cookies.get(REFRESH_COOKIE)
    stored = db.execute(
        "SELECT token_hash FROM refresh_tokens WHERE user_id = %s", (users[PM_EMAIL],)
    ).fetchall()
    assert raw and all(raw not in h for (h,) in stored)
