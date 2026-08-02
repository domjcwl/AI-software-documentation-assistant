"""AI Software Documentation Assistant — Streamlit frontend.

Talks to the FastAPI backend over HTTP only (see api_client.py); it holds
no business logic, no OpenAI key, and never touches the vector store.

Run:
    uvicorn app.main:app --app-dir backend        # backend, port 8000
    streamlit run frontend/app.py                 # this app, port 8501
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import api_client  # noqa: E402
import theme  # noqa: E402
from components import chat, sidebar  # noqa: E402

st.set_page_config(
    page_title="Codebase Assistant",
    page_icon="◆",
    layout="centered",
    initial_sidebar_state="expanded",
)


def init_state() -> None:
    defaults = {
        "theme": "dark",
        "active_project": None,
        "messages": {},  # project_id -> list[{role, content, sources}]
        "conversations": {},  # project_id -> conversation_id
        "pending": None,  # question awaiting an answer on the next rerun
        "sidebar_error": None,
        "sidebar_notice": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def conversation_id(project_id: str) -> str:
    """Stable per (project, chat session). "New chat" drops the entry so a
    fresh id is minted, which also resets the backend's history for it."""
    if project_id not in st.session_state.conversations:
        st.session_state.conversations[project_id] = uuid.uuid4().hex[:12]
    return st.session_state.conversations[project_id]


def main() -> None:
    init_state()
    st.markdown(theme.css(st.session_state.theme), unsafe_allow_html=True)

    backend = check_backend()
    if backend is None:
        return
    sidebar.render()

    project = resolve_active_project()
    if project is None:
        render_no_project(backend)
        return

    project_id = project["project_id"]
    history = st.session_state.messages.setdefault(project_id, [])

    if history:
        chat.render_transcript(project_id)
    else:
        suggestion = chat.render_empty_state(project["name"])
        if suggestion:
            submit(project_id, suggestion)

    # A pending question renders on the rerun after submission, so the user's
    # own message appears immediately rather than only once the answer lands.
    if st.session_state.pending:
        question = st.session_state.pending
        st.session_state.pending = None
        with st.chat_message("assistant", avatar=chat.ASSISTANT_AVATAR):
            result = chat.stream_answer(project_id, conversation_id(project_id), question)
        if result["content"]:
            history.append(
                {"role": "assistant", "content": result["content"], "sources": result["sources"]}
            )
        elif history and history[-1]["role"] == "user":
            # The turn failed; drop the user message rather than leaving a
            # question sitting in the transcript that was never answered.
            history.pop()
        st.rerun()

    if typed := st.chat_input(f"Ask about {project['name'].split('/')[-1]}…"):
        submit(project_id, typed)


def check_backend() -> dict | None:
    """Returns the health payload, or None after rendering a blocking error."""
    try:
        status = api_client.health()
    except api_client.ApiError as exc:
        st.error(f"{exc.message}\n\n{exc.hint}".strip(), icon=":material/cloud_off:")
        return None

    if not status.get("openai_configured"):
        st.warning(
            "OPENAI_API_KEY is not set on the backend. Indexing and answering will fail "
            "until it is added to `.env` and the backend is restarted.",
            icon=":material/key_off:",
        )
    return status


def resolve_active_project() -> dict | None:
    """Pick the active project, defaulting to the first ready one. Also
    self-heals if the selected project was deleted or is still indexing."""
    try:
        projects = api_client.list_projects()
    except api_client.ApiError:
        return None

    ready = [p for p in projects if p["status"] == "ready"]
    if not ready:
        return None

    active_id = st.session_state.active_project
    match = next((p for p in ready if p["project_id"] == active_id), None)
    if match is None:
        match = ready[0]
        st.session_state.active_project = match["project_id"]
    return match


def render_no_project(backend: dict) -> None:
    st.markdown(
        """
        <div class="empty-wrap">
          <h1>Add a GitHub repository to begin</h1>
          <p>Paste a public repo URL in the sidebar. The chat opens as soon as
          indexing finishes.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def submit(project_id: str, question: str) -> None:
    st.session_state.messages.setdefault(project_id, []).append(
        {"role": "user", "content": question}
    )
    st.session_state.pending = question
    st.rerun()


main()
