"""Sidebar: repository list, add-repo form, indexing progress, theme toggle.

One chat per repository — selecting a repo opens its conversation, and
"New chat" starts a fresh conversation for that repo.

While any project is still indexing, the repo list re-renders on a timer
(st.fragment(run_every=...)) so the progress bar advances without the user
touching anything, and without blocking the rest of the page.
"""

from __future__ import annotations

import streamlit as st

import api_client

_TERMINAL = ("ready", "failed")

_STAGE_LABEL = {
    "queued": "queued",
    "fetching": "downloading",
    "scanning": "scanning files",
    "chunking": "chunking",
    "embedding": "embedding",
}


def render() -> None:
    with st.sidebar:
        _new_chat_button()
        _add_repo()
        _repo_section()
        _footer()


def _new_chat_button() -> None:
    if st.button("✏️  New chat", key="new_chat", use_container_width=True):
        project_id = st.session_state.active_project
        if project_id:
            st.session_state.messages.pop(project_id, None)
            # A new conversation id, so the backend starts fresh history too
            # rather than answering against the previous exchange.
            st.session_state.conversations.pop(project_id, None)
        st.rerun()


def _add_repo() -> None:
    # The heading is the text input's own label rather than a separate
    # caption above it: a standalone element sitting on top of a borderless
    # form kept getting overlapped by it.
    with st.form("add_repo", clear_on_submit=True, border=False):
        url = st.text_input("ADD REPOSITORY", placeholder="github.com/owner/repo")
        submitted = st.form_submit_button("Index repository", use_container_width=True)

    if submitted:
        _submit_repo(url)

    if st.session_state.sidebar_error:
        st.error(st.session_state.sidebar_error, icon=":material/error:")
        st.session_state.sidebar_error = None
    if st.session_state.sidebar_notice:
        st.success(st.session_state.sidebar_notice, icon=":material/check_circle:")
        st.session_state.sidebar_notice = None


def _submit_repo(url: str) -> None:
    if not url.strip():
        st.session_state.sidebar_error = "Enter a repository URL first."
        st.rerun()

    try:
        project = api_client.create_project(url.strip())
    except api_client.ApiError as exc:
        st.session_state.sidebar_error = f"{exc.message}\n\n{exc.hint}".strip()
        st.rerun()
        return

    st.session_state.active_project = project["project_id"]
    st.session_state.sidebar_notice = f"Indexing {project['name']}…"
    st.rerun()


def _repo_section() -> None:
    st.caption("REPOSITORIES")
    try:
        projects = api_client.list_projects()
    except api_client.ApiError as exc:
        st.error(f"{exc.message}\n\n{exc.hint}".strip(), icon=":material/cloud_off:")
        return

    if not projects:
        st.caption("No repositories yet — add one above.")
        return

    if any(p["status"] not in _TERMINAL for p in projects):
        _repo_list_live()  # polls while work is in flight
    else:
        _repo_list(projects)


@st.fragment(run_every="1.5s")
def _repo_list_live() -> None:
    """Auto-refreshing variant, used only while something is indexing so the
    app isn't re-running on a timer for no reason once everything is ready."""
    try:
        projects = api_client.list_projects()
    except api_client.ApiError:
        return
    _repo_list(projects)
    if all(p["status"] in _TERMINAL for p in projects):
        # Indexing just finished: rerun the whole app so the chat panel
        # picks up the now-ready project instead of staying disabled.
        st.rerun(scope="app")


def _repo_list(projects: list[dict]) -> None:
    for project in projects:
        project_id = project["project_id"]
        status = project["status"]
        indexing = status not in _TERMINAL
        is_active = project_id == st.session_state.active_project

        short_name = project["name"].split("/")[-1]
        # type="primary" marks the active repo — using Streamlit's own button
        # kind lets the CSS target it, rather than matching on label text.
        if st.button(
            short_name,
            key=f"repo_{project_id}",
            use_container_width=True,
            disabled=indexing,
            type="primary" if is_active else "secondary",
            help=project["name"],
        ):
            st.session_state.active_project = project_id
            st.rerun(scope="app")

        if indexing:
            _progress_row(project_id, status)
        elif status == "failed":
            _failed_row(project_id)


def _progress_row(project_id: str, status: str) -> None:
    try:
        detail = api_client.get_project(project_id)
    except api_client.ApiError:
        return
    percent = int(detail.get("percent", 0))
    label = _STAGE_LABEL.get(detail.get("stage", status), status)
    st.progress(min(max(percent, 0), 100) / 100, text=f"{label} · {percent}%")


def _failed_row(project_id: str) -> None:
    try:
        detail = api_client.get_project(project_id)
    except api_client.ApiError:
        return
    error = detail.get("error") or {}
    message = error.get("message", "Indexing failed.")
    with st.expander("⚠️  Indexing failed", expanded=False):
        st.error(f"{message}\n\n{error.get('hint', '')}".strip(), icon=":material/error:")
        if st.button("Remove", key=f"del_{project_id}", use_container_width=True):
            api_client.delete_project(project_id)
            if st.session_state.active_project == project_id:
                st.session_state.active_project = None
            st.rerun(scope="app")


def _footer() -> None:
    st.markdown("<div style='min-height:1rem'></div>", unsafe_allow_html=True)
    dark = st.session_state.theme == "dark"
    if st.button(
        "🌙  Dark mode" if not dark else "☀️  Light mode",
        key="theme_toggle",
        use_container_width=True,
    ):
        st.session_state.theme = "light" if dark else "dark"
        st.rerun()
