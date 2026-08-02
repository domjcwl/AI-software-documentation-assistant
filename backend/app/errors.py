"""Typed application errors and their HTTP mapping.

Every error the API can return to a client is a subclass of AppError and
renders as {"error": {"code", "message", "hint"}} (planning/api_contract.md).
Route handlers should raise a specific subclass rather than returning error
JSON by hand, so the mapping to status code + shape stays in one place.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")


class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    default_hint: str = ""

    def __init__(self, message: str, hint: str | None = None) -> None:
        self.message = message
        self.hint = hint if hint is not None else self.default_hint
        super().__init__(message)

    def to_body(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "hint": self.hint}}


class InvalidRepoUrlError(AppError):
    code = "invalid_repo_url"
    status_code = 400
    default_hint = "Use a URL like https://github.com/owner/repo, or 'owner/repo'."


class RepoNotFoundError(AppError):
    code = "repo_not_found"
    status_code = 404
    default_hint = "Check the URL. Only public repositories are supported."


class GithubRateLimitedError(AppError):
    code = "github_rate_limited"
    status_code = 429
    default_hint = "Set GITHUB_TOKEN in .env, or retry later."


class RepoTooLargeError(AppError):
    code = "repo_too_large"
    status_code = 413
    default_hint = "Repository exceeds the configured size limit."


class NoIndexableFilesError(AppError):
    code = "no_indexable_files"
    status_code = 422
    default_hint = "No supported source files were found after filtering."


class ProjectNotFoundError(AppError):
    code = "project_not_found"
    status_code = 404
    default_hint = "Check the project_id, or list projects via GET /projects."


class ProjectNotReadyError(AppError):
    code = "project_not_ready"
    status_code = 409
    default_hint = "Wait for indexing to finish, then retry."


class OpenAIAuthError(AppError):
    code = "openai_auth"
    status_code = 401
    default_hint = "OPENAI_API_KEY is missing or invalid."


class OpenAIRateLimitedError(AppError):
    code = "openai_rate_limited"
    status_code = 429
    default_hint = "OpenAI rate limit hit. Retry shortly."


class InternalError(AppError):
    code = "internal_error"
    status_code = 500


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_body())


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: never leak a raw traceback to the client.

    Logs the real exception server-side with a correlation id, and returns
    only that id to the client so it can be matched back to the log.
    """
    correlation_id = uuid.uuid4().hex[:12]
    logger.exception("Unhandled exception [ref=%s] on %s %s", correlation_id, request.method, request.url.path)
    err = InternalError(
        f"An unexpected error occurred (ref: {correlation_id}).",
        hint="Check server logs for this reference id.",
    )
    return JSONResponse(status_code=err.status_code, content=err.to_body())
