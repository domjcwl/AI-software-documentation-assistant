"""Project CRUD endpoints. See planning/api_contract.md.

POST /projects registers the project immediately and schedules the real
indexing pipeline (app.ingestion.pipeline.run_indexing) as a background
task, only when a new record was actually created — reposting an
already-known (repo_url, ref) pair returns the existing project instead
of indexing it twice (see planning/decisions.md ADR-009 for why
BackgroundTasks rather than a separate worker/queue).
"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, BackgroundTasks, status

from app.config import get_settings
from app.ingestion.pipeline import run_indexing
from app.schemas import (
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectDetail,
    ProjectListResponse,
)
from app.services.conversations import conversation_store
from app.services.project_store import project_store
from app.services.repo_url import parse_repo_url
from app.vectorstore.store import vector_store

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_project(payload: ProjectCreateRequest, background_tasks: BackgroundTasks) -> ProjectCreateResponse:
    owner, repo, url_ref = parse_repo_url(payload.repo_url)
    ref = payload.ref or url_ref
    record, created = project_store.create(name=f"{owner}/{repo}", repo_url=payload.repo_url, ref=ref)
    if created:
        background_tasks.add_task(run_indexing, record.project_id)
    return ProjectCreateResponse(project_id=record.project_id, name=record.name, status=record.status)


@router.get("", response_model=ProjectListResponse)
def list_projects() -> ProjectListResponse:
    return ProjectListResponse(projects=[r.to_summary() for r in project_store.list()])


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str) -> ProjectDetail:
    return project_store.get(project_id).to_detail()


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str) -> None:
    """Removes the registry entry, the Chroma collection, the extracted
    repo, and its manifest JSON — idempotent, like the underlying store
    and vector-store delete calls it wraps."""
    settings = get_settings()
    project_store.delete(project_id)
    vector_store.delete_collection(project_id)
    conversation_store.clear_project(project_id)
    shutil.rmtree(settings.repo_dir / project_id, ignore_errors=True)
    (settings.repo_dir / f"{project_id}.manifest.json").unlink(missing_ok=True)
