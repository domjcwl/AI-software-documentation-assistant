"""Project registry, persisted to a JSON file with atomic writes so
projects survive a process restart (planning/decisions.md ADR-008) —
re-embedding a large repo is expensive, so losing the registry on restart
would silently orphan the Chroma collections still sitting on disk.

Status transitions are driven by app.ingestion.pipeline.run_indexing via
update().
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.errors import ProjectNotFoundError
from app.schemas import ErrorDetail, ProjectDetail, ProjectStatus, ProjectSummary

logger = logging.getLogger("app.services.project_store")


@dataclass
class ProjectRecord:
    project_id: str
    name: str
    repo_url: str
    ref: str | None
    status: ProjectStatus
    stage: str
    percent: int
    message: str
    created_at: datetime
    files_scanned: int = 0
    files_indexed: int = 0
    chunks_embedded: int = 0
    truncated: bool = False
    error: ErrorDetail | None = None

    def to_summary(self) -> ProjectSummary:
        return ProjectSummary(
            project_id=self.project_id,
            name=self.name,
            repo_url=self.repo_url,
            ref=self.ref,
            status=self.status,
            files_indexed=self.files_indexed,
            chunks=self.chunks_embedded,
            created_at=self.created_at,
        )

    def to_detail(self) -> ProjectDetail:
        return ProjectDetail(
            project_id=self.project_id,
            name=self.name,
            status=self.status,
            stage=self.stage,
            percent=self.percent,
            message=self.message,
            files_scanned=self.files_scanned,
            files_indexed=self.files_indexed,
            chunks_embedded=self.chunks_embedded,
            truncated=self.truncated,
            error=self.error,
        )

    def to_json_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "repo_url": self.repo_url,
            "ref": self.ref,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
            "files_scanned": self.files_scanned,
            "files_indexed": self.files_indexed,
            "chunks_embedded": self.chunks_embedded,
            "truncated": self.truncated,
            "error": self.error.model_dump() if self.error else None,
        }

    @classmethod
    def from_json_dict(cls, data: dict) -> ProjectRecord:
        error = ErrorDetail(**data["error"]) if data.get("error") else None
        return cls(
            project_id=data["project_id"],
            name=data["name"],
            repo_url=data["repo_url"],
            ref=data.get("ref"),
            status=data["status"],
            stage=data["stage"],
            percent=data["percent"],
            message=data["message"],
            created_at=datetime.fromisoformat(data["created_at"]),
            files_scanned=data.get("files_scanned", 0),
            files_indexed=data.get("files_indexed", 0),
            chunks_embedded=data.get("chunks_embedded", 0),
            truncated=data.get("truncated", False),
            error=error,
        )


class ProjectStore:
    """Thread-safe CRUD for project records, optionally persisted to disk.

    persist_path=None (used by tests) keeps everything in memory only —
    see backend/conftest.py, which points the process-wide singleton at
    None for the duration of the test suite so tests never touch the
    real on-disk registry.
    """

    def __init__(self, persist_path: Path | None = None) -> None:
        self._projects: dict[str, ProjectRecord] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._load()

    def create(self, name: str, repo_url: str, ref: str | None) -> tuple[ProjectRecord, bool]:
        """Create a project, or return the existing one for the same
        (repo_url, ref) pair rather than indexing it twice. The bool is
        True only when a new record was actually created — callers use
        it to decide whether to kick off a new indexing job."""
        with self._lock:
            for record in self._projects.values():
                if record.repo_url == repo_url and record.ref == ref:
                    return record, False
            record = ProjectRecord(
                project_id=uuid.uuid4().hex[:12],
                name=name,
                repo_url=repo_url,
                ref=ref,
                status="queued",
                stage="queued",
                percent=0,
                message="Queued for indexing.",
                created_at=datetime.now(timezone.utc),
            )
            self._projects[record.project_id] = record
            snapshot = list(self._projects.values())
        self._save(snapshot)
        return record, True

    def list(self) -> list[ProjectRecord]:
        with self._lock:
            return sorted(self._projects.values(), key=lambda r: r.created_at, reverse=True)

    def get(self, project_id: str) -> ProjectRecord:
        with self._lock:
            record = self._projects.get(project_id)
        if record is None:
            raise ProjectNotFoundError(f"No project with id {project_id!r}.")
        return record

    def update(self, project_id: str, **changes: object) -> ProjectRecord:
        """Apply field updates to a project record — the ingestion
        pipeline's progress-reporting hook (app.ingestion.pipeline)."""
        with self._lock:
            record = self._projects.get(project_id)
            if record is None:
                raise ProjectNotFoundError(f"No project with id {project_id!r}.")
            for key, value in changes.items():
                if not hasattr(record, key):
                    raise ValueError(f"Unknown project field: {key!r}")
                setattr(record, key, value)
            snapshot = list(self._projects.values())
        self._save(snapshot)
        return record

    def delete(self, project_id: str) -> None:
        """Idempotent: deleting an already-gone project is a no-op success."""
        with self._lock:
            self._projects.pop(project_id, None)
            snapshot = list(self._projects.values())
        self._save(snapshot)

    def count_ready(self) -> int:
        with self._lock:
            return sum(1 for r in self._projects.values() if r.status == "ready")

    def clear(self) -> None:
        """Test-only helper to reset in-memory state between test cases.
        Deliberately does not touch disk — see ProjectStore docstring."""
        with self._lock:
            self._projects.clear()

    def _load(self) -> None:
        if self._persist_path is None or not self._persist_path.exists():
            return
        try:
            raw = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for item in raw.get("projects", []):
                record = ProjectRecord.from_json_dict(item)
                self._projects[record.project_id] = record
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Could not load project registry from %s: %s", self._persist_path, exc)

    def _save(self, records: list[ProjectRecord]) -> None:
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"projects": [r.to_json_dict() for r in records]}
            tmp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, self._persist_path)  # atomic on both POSIX and Windows
        except OSError as exc:
            logger.warning("Could not persist project registry to %s: %s", self._persist_path, exc)


project_store = ProjectStore(persist_path=get_settings().projects_file)
