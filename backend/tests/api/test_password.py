"""Temporary passwords, first-login change, own password change, Owner reset (C5.1b)."""

# ruff: noqa: F811  (fixtures imported from test_users are used as parameters)

import uuid
from collections.abc import Callable

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.api.test_users import (  # noqa: F401 (fixtures)
    DOMAIN,
    PASSWORD,
    _requires_jwt_secret,
    make_user,
    owner,
    session,
)

NEW_PASSWORD = "brand new pass 2026"  # noqa: S105 (test-only)
MakeUser = Callable[[str, str], uuid.UUID]


def change(client: TestClient, current: str, new: str) -> object:
    return client.post("/auth/password", json={"current_password": current, "new_password": new})


def created_by_owner(owner: TestClient, name: str) -> tuple[str, str]:
    r = owner.post("/users", json={"email": f"{name}{DOMAIN}", "full_name": name,
                                   "role": "inventory_manager"})  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["temp_password"]


# ------------------------------------------------------------------ first login
def test_owner_created_user_must_change_the_temp_password_first(owner: TestClient) -> None:
    _, temp = created_by_owner(owner, "newbie")
    client = session("newbie", temp)
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    # everything except me/password/logout/refresh is closed until the password is changed
    for path in ("/dashboard", "/chat/threads"):
        r = client.get(path)
        assert r.status_code == 403 and r.json() == {"detail": "Please change your password first."}
    r = change(client, temp, NEW_PASSWORD)
    assert r.status_code == 200 and r.json()["must_change_password"] is False
    assert client.get("/chat/threads").status_code == 200  # same browser stays signed in
    assert session("newbie", NEW_PASSWORD).get("/auth/me").status_code == 200


def test_seeded_and_existing_users_are_not_forced(make_user: MakeUser) -> None:
    make_user("procurement_manager", "veteran")
    assert session("veteran").get("/auth/me").json()["must_change_password"] is False


# ------------------------------------------------------------------ own change
def test_change_ends_every_other_session(make_user: MakeUser) -> None:
    make_user("procurement_manager", "pm")
    laptop, phone = session("pm"), session("pm")
    assert change(laptop, PASSWORD, NEW_PASSWORD).status_code == 200
    assert laptop.get("/auth/me").status_code == 200
    assert phone.get("/auth/me").status_code == 401
    assert phone.post("/auth/refresh").status_code == 401


@pytest.mark.parametrize(
    ("current", "new", "message"),
    [
        ("wrong password", NEW_PASSWORD, "Your current password is incorrect."),
        (PASSWORD, "short", "Use at least 10 characters."),
        (PASSWORD, PASSWORD, "Choose a password different from the current one."),
        (PASSWORD, f"pm{DOMAIN}", "Don't use your email as your password."),
    ],
)
def test_bad_changes_are_refused(make_user: MakeUser, current: str, new: str, message: str) -> None:
    make_user("procurement_manager", "pm")
    client = session("pm")
    r = change(client, current, new)
    assert r.status_code == 400 and r.json() == {"detail": message}
    assert session("pm").get("/auth/me").status_code == 200  # old password still works


def test_wrong_current_password_is_rate_limited(make_user: MakeUser) -> None:
    make_user("procurement_manager", "pm")
    client = session("pm")
    codes = [change(client, "wrong password", NEW_PASSWORD).status_code for _ in range(6)]
    assert codes[:5] == [400] * 5 and codes[5] == 429


def test_anonymous_cannot_change_a_password() -> None:
    assert change(TestClient(app), PASSWORD, NEW_PASSWORD).status_code == 401


# ------------------------------------------------------------------ owner reset
def test_owner_reset_gives_a_new_temp_password_and_logs_the_user_out(
    owner: TestClient, db: psycopg.Connection
) -> None:
    user_id, temp = created_by_owner(owner, "forgetful")
    old = session("forgetful", temp)
    change(old, temp, NEW_PASSWORD)
    r = owner.post(f"/users/{user_id}/reset-password")
    assert r.status_code == 200
    new_temp = r.json()["temp_password"]
    assert new_temp not in (temp, NEW_PASSWORD) and "password_hash" not in r.text
    assert old.get("/auth/me").status_code == 401  # signed out everywhere
    fresh = session("forgetful", new_temp)
    assert fresh.get("/auth/me").json()["must_change_password"] is True
    row = db.execute(
        "SELECT before_state, after_state FROM agent_audit_log "
        "WHERE operation = 'reset_password' AND entity_id = %s",
        (user_id,),
    ).fetchone()
    assert row is not None and new_temp not in str(row)  # audited, without the secret


def test_owner_resets_own_password_by_changing_it(owner: TestClient) -> None:
    me = owner.get("/auth/me").json()["id"]
    r = owner.post(f"/users/{me}/reset-password")
    assert r.status_code == 400


@pytest.mark.parametrize("role", ["inventory_manager", "procurement_manager"])
def test_only_the_owner_can_reset(make_user: MakeUser, role: str) -> None:
    target = make_user("inventory_manager", "target")
    make_user(role, "someone")
    assert session("someone").post(f"/users/{target}/reset-password").status_code == 403


def test_reset_of_unknown_user_is_404(owner: TestClient) -> None:
    assert owner.post(f"/users/{uuid.uuid4()}/reset-password").status_code == 404
