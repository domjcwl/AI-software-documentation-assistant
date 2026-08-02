"""In-memory conversation history, keyed by (project_id, conversation_id).

Deliberately not persisted (planning/decisions.md ADR-008): losing chat
scrollback on restart is a minor annoyance, where losing an indexed repo
would mean re-paying the embedding cost — so projects go to disk and this
does not. The interface is narrow enough that a SQLite implementation is
a drop-in replacement.
"""

from __future__ import annotations

import threading

from app.agents.state import Turn

_MAX_TURNS = 10  # 5 exchanges


class ConversationStore:
    def __init__(self, max_turns: int = _MAX_TURNS) -> None:
        self._turns: dict[tuple[str, str], list[Turn]] = {}
        self._lock = threading.Lock()
        self._max_turns = max_turns

    def get(self, project_id: str, conversation_id: str) -> list[Turn]:
        with self._lock:
            return list(self._turns.get((project_id, conversation_id), []))

    def append(self, project_id: str, conversation_id: str, role: str, content: str) -> None:
        with self._lock:
            key = (project_id, conversation_id)
            turns = self._turns.setdefault(key, [])
            turns.append(Turn(role=role, content=content))
            if len(turns) > self._max_turns:
                del turns[: len(turns) - self._max_turns]

    def clear_project(self, project_id: str) -> None:
        """Called when a project is deleted, so its conversations don't
        linger in memory referencing a project that no longer exists."""
        with self._lock:
            for key in [k for k in self._turns if k[0] == project_id]:
                del self._turns[key]

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()


conversation_store = ConversationStore()
