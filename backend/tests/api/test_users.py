"""Owner-only user management (C3.2)."""

import uuid
from collections.abc import Callable, Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.auth.rate_limit import login_limiter
from app.auth.tokens import REFRESH_COOKIE
from app.core.config import get_settings
from app.main import app

PASSWORD = "a long test-only password"  # noqa: S105
DOMAIN = "@test.inventorymantra"


@pytest.fixture(scope="module", autouse=True)
def _requires_jwt_secret() -> None:
    try:
        get_settings().jwt_secret()
    except RuntimeError:
        pytest.skip("JWT_SECRET_KEY (>= 32 chars) not set")


@pytest.fixture
def make_user(db: psycopg.Connection) -> Iterator[Callable[[str, str], uuid.UUID]]:
    """Create users with a known password; everything on the test domain is removed after."""

    def make(role: str, name: str) -> uuid.UUID:
        user_id = uuid.uuid4()
        db.execute(
            """INSERT INTO users (id, email, password_hash, full_name, role)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, f"{name}{DOMAIN}", hash_password(PASSWORD), f"Test {name}", role),
        )
        return user_id

    login_limiter.clear()
    yield make
    login_limiter.clear()
    test_users = "SELECT id FROM users WHERE email LIKE %s"
    like = f"%{DOMAIN}"
    db.execute(f"DELETE FROM agent_audit_log WHERE user_id IN ({test_users})", (like,))  # noqa: S608
    db.execute(f"DELETE FROM refresh_tokens WHERE user_id IN ({test_users})", (like,))  # noqa: S608
    db.execute(
        f"DELETE FROM notifications WHERE recipient_user_id IN ({test_users})",  # noqa: S608
        (like,),
    )
    db.execute("DELETE FROM users WHERE email LIKE %s", (like,))


def session(name: str, password: str = PASSWORD) -> TestClient:
    client = TestClient(app)
    r = client.post("/auth/login", json={"email": f"{name}{DOMAIN}", "password": password})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def owner(make_user: Callable[[str, str], uuid.UUID]) -> TestClient:
    make_user("owner", "boss")
    return session("boss")


NEW_USER = {"email": "  Priya@Test.InventoryMantra ", "full_name": "Priya Sharma",
            "role": "inventory_manager"}  # fmt: skip


# ------------------------------------------------------------------ access control
@pytest.mark.parametrize("role", ["inventory_manager", "procurement_manager"])
def test_non_owners_are_forbidden(make_user: Callable[[str, str], uuid.UUID], role: str) -> None:
    target = make_user("inventory_manager", "target")
    make_user(role, "someone")
    client = session("someone")
    assert client.get("/users").status_code == 403
    assert client.post("/users", json=NEW_USER).status_code == 403
    assert client.post(f"/users/{target}/deactivate").status_code == 403


def test_anonymous_is_unauthorized() -> None:
    assert TestClient(app).get("/users").status_code == 401


# ------------------------------------------------------------------ list
def test_owner_lists_users_without_secrets(owner: TestClient) -> None:
    r = owner.get("/users")
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"owner@demo.inventorymantra", f"boss{DOMAIN}"} <= emails
    assert "password" not in r.text and "$2b$" not in r.text and "token" not in r.text


# ------------------------------------------------------------------ create
def test_owner_creates_user_who_can_log_in_with_the_temp_password(
    owner: TestClient, db: psycopg.Connection
) -> None:
    r = owner.post("/users", json=NEW_USER)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == f"priya{DOMAIN}"  # normalised
    assert body["role"] == "inventory_manager" and body["is_active"] is True
    assert body["created_by_name"] == "Test boss"
    assert len(body["temp_password"]) >= 12
    new = session("priya", body["temp_password"])
    assert new.get("/auth/me").json()["role"] == "inventory_manager"
    audit = db.execute(
        "SELECT operation, after_state::text FROM agent_audit_log WHERE entity_id = %s",
        (body["id"],),
    ).fetchone()
    assert audit is not None and audit[0] == "create_user"
    assert body["temp_password"] not in audit[1]  # the password is never audited


def test_duplicate_email_is_a_conflict(owner: TestClient) -> None:
    assert owner.post("/users", json=NEW_USER).status_code == 201
    dupe = dict(NEW_USER, email="PRIYA@test.inventorymantra")
    assert owner.post("/users", json=dupe).status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        dict(NEW_USER, role="admin"),
        dict(NEW_USER, role="OWNER"),
        dict(NEW_USER, email="not-an-email"),
        dict(NEW_USER, full_name=""),
        dict(NEW_USER, password="chosen-by-caller"),  # noqa: S106 (server generates passwords)
        dict(NEW_USER, is_active=False),
    ],
)
def test_invalid_payloads_are_rejected(owner: TestClient, payload: dict[str, object]) -> None:
    assert owner.post("/users", json=payload).status_code == 422


# ------------------------------------------------------------------ deactivate / activate
def test_deactivation_ends_all_sessions_immediately(
    owner: TestClient, make_user: Callable[[str, str], uuid.UUID]
) -> None:
    target = make_user("procurement_manager", "leaver")
    victim = session("leaver")
    stolen_refresh = victim.cookies.get(REFRESH_COOKIE)
    assert victim.get("/auth/me").status_code == 200

    r = owner.post(f"/users/{target}/deactivate")
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert victim.get("/auth/me").status_code == 401  # live access token is dead
    replay = TestClient(app)
    replay.cookies.set(REFRESH_COOKIE, stolen_refresh or "")
    assert replay.post("/auth/refresh").status_code == 401
    bad = TestClient(app).post(
        "/auth/login", json={"email": f"leaver{DOMAIN}", "password": PASSWORD}
    )
    assert bad.status_code == 401

    assert owner.post(f"/users/{target}/activate").json()["is_active"] is True
    assert session("leaver").get("/auth/me").status_code == 200


def test_owner_cannot_deactivate_themselves(owner: TestClient) -> None:
    me = owner.get("/auth/me").json()["id"]
    assert owner.post(f"/users/{me}/deactivate").status_code == 400
    assert owner.get("/auth/me").status_code == 200


def test_unknown_user_is_404(owner: TestClient) -> None:
    assert owner.post(f"/users/{uuid.uuid4()}/deactivate").status_code == 404
    assert owner.post("/users/not-a-uuid/deactivate").status_code == 422


def test_deactivation_is_audited(
    owner: TestClient, make_user: Callable[[str, str], uuid.UUID], db: psycopg.Connection
) -> None:
    target = make_user("inventory_manager", "audited")
    owner.post(f"/users/{target}/deactivate")
    row = db.execute(
        """SELECT operation, before_state->>'is_active', after_state->>'is_active', role
           FROM agent_audit_log WHERE entity_id = %s""",
        (str(target),),
    ).fetchone()
    assert row == ("deactivate_user", "true", "false", "owner")
