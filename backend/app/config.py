"""Application configuration.

Every setting the app reads lives here, sourced from environment variables
or the project-root `.env` file — no `os.getenv` calls scattered through
the codebase. See planning/architecture.md §7 for the full key reference
and planning/decisions.md ADR-011 for why Python 3.14 was kept.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[2] is the project root, regardless of
# the process's current working directory when uvicorn/pytest starts.
_ROOT_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _ROOT_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- OpenAI / GitHub credentials (required once agents are wired in, Phase 3+) ---
    openai_api_key: str | None = Field(default=None)
    github_token: str | None = Field(default=None)

    # --- Models. Verify current OpenAI model IDs before Phase 3 wiring —
    # these defaults are placeholders and a stale ID fails silently at
    # call time, not at startup. See planning/decisions.md ADR-011. ---
    openai_chat_model: str = "gpt-4o-mini"
    openai_fast_model: str = "gpt-4o-mini"
    openai_embed_model: str = "text-embedding-3-small"

    # --- Storage (project-root-relative, not cwd-relative — see the
    # validator below, which is what actually enforces this for values
    # coming from .env, not just for the class defaults here) ---
    chroma_dir: Path = _ROOT_DIR / "data" / "chroma"
    repo_dir: Path = _ROOT_DIR / "data" / "repos"
    projects_file: Path = _ROOT_DIR / "data" / "projects.json"

    @field_validator("chroma_dir", "repo_dir", "projects_file", mode="after")
    @classmethod
    def _resolve_relative_to_project_root(cls, v: Path) -> Path:
        """.env.example ships CHROMA_DIR=./data/chroma-style relative
        paths, which — without this — resolve against the process's cwd
        at the point of use, not the project root. That silently scatters
        data/ under wherever uvicorn happened to be launched from (caught
        live: running from backend/ put everything in backend/data/).
        Absolute overrides pass through unchanged."""
        return v if v.is_absolute() else (_ROOT_DIR / v).resolve()

    # --- Ingestion limits (Phase 1+, unused until the pipeline exists) ---
    max_repo_bytes: int = 150 * 1024 * 1024
    max_file_bytes: int = 400 * 1024
    max_files: int = 3000

    # --- Retrieval / agents (Phase 3+, unused until the graph exists) ---
    retrieval_k: int = 8
    max_context_chunks: int = 12
    max_revisions: int = 2

    # --- Server ---
    backend_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:8501"

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings. Call sites should depend on this,
    not construct Settings() directly, so tests can override cleanly."""
    return Settings()
