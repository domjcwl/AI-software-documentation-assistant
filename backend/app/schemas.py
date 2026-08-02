"""Pydantic request/response models. Every shape in planning/api_contract.md
that is implemented so far lives here — routers import from this module
rather than declaring inline models, so the wire contract stays in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["queued", "fetching", "scanning", "chunking", "embedding", "ready", "failed"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    hint: str = ""


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    openai_configured: bool
    chroma_ok: bool
    projects_indexed: int


class ProjectCreateRequest(BaseModel):
    repo_url: str = Field(..., min_length=1, description="Public GitHub repo URL, or 'owner/repo'.")
    ref: str | None = Field(default=None, description="Branch, tag, or commit SHA.")


class ProjectCreateResponse(BaseModel):
    project_id: str
    name: str
    status: ProjectStatus


class ProjectSummary(BaseModel):
    project_id: str
    name: str
    repo_url: str
    ref: str | None
    status: ProjectStatus
    files_indexed: int = 0
    chunks: int = 0
    created_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectSummary]


class ProjectDetail(BaseModel):
    project_id: str
    name: str
    status: ProjectStatus
    stage: str
    percent: int = Field(ge=0, le=100)
    message: str
    files_scanned: int = 0
    files_indexed: int = 0
    chunks_embedded: int = 0
    truncated: bool = False
    error: ErrorDetail | None = None


class ChatRequest(BaseModel):
    project_id: str
    conversation_id: str | None = None
    message: str = Field(..., min_length=1)
