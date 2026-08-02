"""GET /health — liveness and dependency status. See planning/api_contract.md."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import get_settings
from app.schemas import HealthResponse
from app.services.project_store import project_store
from app.vectorstore.store import vector_store

logger = logging.getLogger("app.health")

router = APIRouter(tags=["health"])

APP_VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=APP_VERSION,
        openai_configured=settings.openai_configured,
        chroma_ok=_check_chroma(),
        projects_indexed=project_store.count_ready(),
    )


def _check_chroma() -> bool:
    """Best-effort check that the shared Chroma client can actually serve
    a query. A broken vector store must surface as chroma_ok: false,
    never as a 500 from the healthcheck itself — that would make health
    unusable for exactly the situation it's meant to diagnose.
    """
    try:
        vector_store.count("__healthcheck__")  # any project_id; a missing collection is a normal 0, not an error
        return True
    except Exception:
        logger.exception("Chroma healthcheck failed")
        return False
