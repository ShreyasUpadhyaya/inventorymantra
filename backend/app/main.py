"""FastAPI entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent.tracing import configure_tracing
from app.api.routes import auth, chat, dashboard, users
from app.core.config import app_today, get_settings
from app.core.logging import configure_logging
from app.db.pool import close_pool

configure_logging()
settings = get_settings()


logger = logging.getLogger("inventorymantra")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        settings.jwt_secret()
    except RuntimeError as exc:  # keep /health up, but make the problem obvious
        logger.error("Login disabled: %s Check the exact name in .env, then restart.", exc)
    if configure_tracing():
        logger.info("LangSmith tracing on (project %s).", settings.langsmith_project)
    yield
    close_pool()


app = FastAPI(title="InventoryMantra API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dashboard.router)
app.include_router(chat.router)


class Health(BaseModel):
    status: str
    llm_mode: str
    today: str


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", llm_mode=settings.llm_mode, today=app_today().isoformat())
