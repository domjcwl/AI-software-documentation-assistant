"""FastAPI application factory and entry point.

Run with: uvicorn app.main:app --reload --app-dir backend
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, health, projects
from app.config import get_settings
from app.errors import AppError, app_error_handler, unhandled_exception_handler

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Software Documentation Assistant", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # AppError subclasses are matched via MRO, so this one registration
    # covers every typed error in app.errors.
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(chat.router)

    return app


app = create_app()
