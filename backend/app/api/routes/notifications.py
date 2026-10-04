"""Your notifications (C6.5): newest first, unread count, mark as read.

GET  /notifications            {unread, items[]} for the signed-in user only
POST /notifications/{id}/read  mark one as read (only your own)
POST /notifications/read-all

Opening the list as a Procurement Manager or Owner also runs the day's reorder scan
(de-duplicated, so it adds each item at most once a day): the "daily scan, plus one on
dashboard load" of DECISION D-011 without a scheduler for the demo.
"""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import Conn, CurrentUser
from app.core.config import app_today
from app.services.notifications import scan_reorder

router = APIRouter(prefix="/notifications", tags=["notifications"])
logger = logging.getLogger("inventorymantra.notifications")
LIMIT = 30


class NotificationOut(BaseModel):
    id: UUID
    type: str
    title: str
    body: str | None
    payload: dict[str, Any]
    created_at: datetime
    read: bool


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


@router.get("", response_model=NotificationsOut)
def mine(user: CurrentUser, conn: Conn) -> NotificationsOut:
    if user.role in ("procurement_manager", "owner"):
        try:
            scan_reorder(app_today())
        except Exception:  # the list must still load if the scan can't run
            logger.exception("reorder scan failed")
    rows = conn.execute(
        """SELECT id, type, title, body, payload, created_at, read_at IS NOT NULL
           FROM notifications WHERE recipient_user_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (user.id, LIMIT),
    ).fetchall()
    unread = conn.execute(
        "SELECT count(*) FROM notifications WHERE recipient_user_id = %s AND read_at IS NULL",
        (user.id,),
    ).fetchone()
    items = [NotificationOut(id=r[0], type=r[1], title=r[2], body=r[3], payload=r[4],
                             created_at=r[5], read=r[6]) for r in rows]  # fmt: skip
    return NotificationsOut(unread=unread[0] if unread else 0, items=items)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def read_all(user: CurrentUser, conn: Conn) -> None:
    conn.execute(
        "UPDATE notifications SET read_at = now() WHERE recipient_user_id = %s AND read_at IS NULL",
        (user.id,),
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def read_one(notification_id: UUID, user: CurrentUser, conn: Conn) -> None:
    cur = conn.execute(
        "UPDATE notifications SET read_at = coalesce(read_at, now()) "
        "WHERE id = %s AND recipient_user_id = %s",
        (notification_id, user.id),
    )
    if cur.rowcount == 0:  # someone else's, or none: the same answer
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")
