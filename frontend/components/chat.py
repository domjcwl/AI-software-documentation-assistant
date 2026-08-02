"""Chat transcript, streamed answer, and the sources panel.

Consumes the NDJSON event stream from POST /chat (see
planning/api_contract.md). The `revision` event is the interesting one:
the backend streams a draft answer optimistically and, if the Review agent
rejects it, tells the client to discard what it has shown and start again
(planning/decisions.md ADR-005). That is why the answer is rendered into a
placeholder that can be cleared mid-flight.
"""

from __future__ import annotations

import streamlit as st

import api_client

# Avatars must be a preset name, a true emoji, or a `:material/...:` icon.
# A decorative glyph like "◆" is NOT emoji-classified, so Streamlit falls
# through to treating it as an image path and raises.
ASSISTANT_AVATAR = ":material/auto_awesome:"
USER_AVATAR = "user"  # preset; hidden by CSS, since ChatGPT shows no user avatar

SUGGESTIONS = [
    "How does authentication work?",
    "Summarize the project architecture",
    "Explain the project's file directory",
    "Where should I modify the code to add Google Login?",
]

_PHASE_TEXT = {
    "coordinating": "Understanding the question",
    "retrieving": "Searching the codebase",
    "explaining": "Writing the answer",
    "reviewing": "Checking the answer",
    "revising": "Refining answer with additional context",
    "done": "Done",
}

_ROUTE_LABEL = {
    "code_qa": "code",
    "architecture": "architecture",
    "directory": "file tree",
    "modification": "change plan",
    "clarify": "needs detail",
}


def render_transcript(project_id: str) -> None:
    for message in st.session_state.messages.get(project_id, []):
        avatar = USER_AVATAR if message["role"] == "user" else ASSISTANT_AVATAR
        with st.chat_message(message["role"], avatar=avatar):
            st.markdown(message["content"])
            if message.get("sources"):
                render_sources(message["sources"])


def render_sources(sources: list[dict]) -> None:
    """Collapsed by default — citations should be available on demand
    without pushing the answer off the screen."""
    if not sources:
        return
    with st.expander(f"📄  {len(sources)} sources", expanded=False):
        for source in sources:
            ref = f"{source['path']}:{source['start_line']}-{source['end_line']}"
            score = source.get("score")
            meta = f"<span class='cite'>{ref}</span>"
            if score is not None:
                meta += f"<span class='cite'>similarity {score:.2f}</span>"
            st.markdown(meta, unsafe_allow_html=True)
            st.code(source.get("snippet", ""), language=source.get("language", "text"))


def render_empty_state(project_name: str) -> str | None:
    """Centred greeting plus starter prompts. Returns a suggestion if one
    was clicked, so the caller can treat it exactly like typed input."""
    st.markdown(
        f"""
        <div class="empty-wrap">
          <h1>Ask anything about <em>{project_name.split('/')[-1]}</em></h1>
          <p>Answers cite the exact files and line numbers they came from.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    chosen = None
    left, right = st.columns(2, gap="small")
    for i, suggestion in enumerate(SUGGESTIONS):
        column = left if i % 2 == 0 else right
        if column.button(suggestion, key=f"sugg_{i}", use_container_width=True):
            chosen = suggestion
    return chosen


def stream_answer(project_id: str, conversation_id: str, question: str) -> dict:
    """Render one streamed answer. Returns {content, sources, meta}; an
    empty `content` means the turn failed and should not be appended to
    the transcript."""
    phase_slot = st.empty()
    sources_slot = st.empty()
    answer_slot = st.empty()

    buffer = ""
    sources: list[dict] = []
    final: dict = {}

    def show_phase(label: str) -> None:
        phase_slot.markdown(
            f"<div class='phase'><span class='dot'></span>{label}</div>",
            unsafe_allow_html=True,
        )

    show_phase(_PHASE_TEXT["coordinating"])

    try:
        events = api_client.stream_chat(project_id, conversation_id, question)
        for event in events:
            kind = event.get("type")

            if kind == "phase":
                show_phase(_PHASE_TEXT.get(event["phase"], event["phase"]))

            elif kind == "sources":
                sources = event.get("sources") or []
                with sources_slot.container():
                    render_sources(sources)

            elif kind == "token":
                buffer += event.get("text", "")
                answer_slot.markdown(buffer + "▌")

            elif kind == "revision":
                # Review rejected the draft: discard what the user has already
                # seen and stream the replacement (ADR-005).
                buffer = ""
                answer_slot.empty()
                sources_slot.empty()
                show_phase(_PHASE_TEXT["revising"])

            elif kind == "final":
                final = event
                # Authoritative text — may differ from the streamed tokens if
                # Review returned a polished rewrite.
                buffer = event.get("answer", buffer)

            elif kind == "error":
                error = event.get("error", {})
                phase_slot.empty()
                answer_slot.error(
                    f"{error.get('message', 'The assistant failed to answer.')}\n\n"
                    f"{error.get('hint', '')}".strip(),
                    icon=":material/error:",
                )
                return {"content": "", "sources": [], "meta": {}}

    except api_client.ApiError as exc:
        phase_slot.empty()
        answer_slot.error(f"{exc.message}\n\n{exc.hint}".strip(), icon=":material/error:")
        return {"content": "", "sources": [], "meta": {}}

    phase_slot.empty()
    answer_slot.markdown(buffer)

    if final:
        _render_answer_meta(final)

    return {"content": buffer, "sources": sources, "meta": final}


def _render_answer_meta(final: dict) -> None:
    """Small footer showing which workflow ran — the visible evidence that
    four distinct agents are doing the work."""
    bits = []
    route = final.get("route")
    if route:
        bits.append(_ROUTE_LABEL.get(route, route))
    if final.get("revisions"):
        bits.append(f"{final['revisions']} revision(s)")
    if final.get("low_confidence"):
        bits.append("low confidence")
    if bits:
        st.markdown(
            "<div class='meta-row'>" + " · ".join(bits) + "</div>", unsafe_allow_html=True
        )
