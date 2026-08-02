"""ChatGPT-style theming, injected as CSS.

Streamlit's own theme is set at config level and can't be swapped at
runtime, so instead both palettes are defined here as CSS custom
properties and the active one is written into the page on each rerun.
Toggling the theme just reruns the script with a different palette —
no client-side JS, no config reload.

The `data-testid` selectors below (stAppViewContainer, stSidebar,
stChatInput, ...) were verified against the installed streamlit==1.60.0
bundle rather than copied from a blog post; they do shift between
Streamlit versions, so re-check them after any upgrade.
"""

from __future__ import annotations

DARK = {
    "app_bg": "#212121",
    "sidebar_bg": "#171717",
    "text": "#ececec",
    "text_muted": "#9b9b9b",
    "border": "#2f2f2f",
    "user_bubble": "#2f2f2f",
    "hover": "#2a2a2a",
    "active": "#2f2f2f",
    "input_bg": "#303030",
    "input_border": "#4a4a4a",
    "code_bg": "#1a1a1a",
    "accent": "#10a37f",
    "accent_text": "#ffffff",
}

LIGHT = {
    "app_bg": "#ffffff",
    "sidebar_bg": "#f9f9f9",
    "text": "#0d0d0d",
    "text_muted": "#676767",
    "border": "#e5e5e5",
    "user_bubble": "#f4f4f4",
    "hover": "#ececec",
    "active": "#e3e3e3",
    "input_bg": "#ffffff",
    "input_border": "#d5d5d5",
    "code_bg": "#f7f7f7",
    "accent": "#10a37f",
    "accent_text": "#ffffff",
}

PALETTES = {"dark": DARK, "light": LIGHT}


def css(theme: str) -> str:
    p = PALETTES.get(theme, DARK)
    return f"""
<style>
:root {{
  --app-bg: {p["app_bg"]};
  --sidebar-bg: {p["sidebar_bg"]};
  --text: {p["text"]};
  --text-muted: {p["text_muted"]};
  --border: {p["border"]};
  --user-bubble: {p["user_bubble"]};
  --hover: {p["hover"]};
  --active: {p["active"]};
  --input-bg: {p["input_bg"]};
  --input-border: {p["input_border"]};
  --code-bg: {p["code_bg"]};
  --accent: {p["accent"]};
  --accent-text: {p["accent_text"]};
}}

/* --- Chrome removal ---
   The header is made transparent rather than hidden: when the sidebar is
   collapsed, its re-open control lives in the header, so `display:none`
   here leaves no way to get the sidebar back. Only the toolbar/deploy/
   status widgets are actually removed. */
/* Transparent, but its natural height and overflow are left alone: the
   toolbar (and with it the sidebar re-open control) lives inside the
   header, and forcing the height risked clipping it. */
[data-testid="stAppHeader"] {{
  background: transparent !important;
  box-shadow: none !important;
}}
/* NOTE: stToolbar is deliberately NOT hidden. Streamlit renders the
   sidebar's re-open control (stExpandSidebarButton) *inside* the toolbar,
   so `display:none` on it makes a collapsed sidebar unrecoverable. Only
   the toolbar's unwanted children are removed. */
[data-testid="stAppDeployButton"],
[data-testid="stMainMenu"],
[data-testid="stMainMenuButton"],
[data-testid="stStatusWidget"],
[data-testid="stConnectionStatus"],
[data-testid="stDecoration"],
/* Streamlit's own "install the skills" promo, which floats over the app. */
[data-testid="stSkillsNudge"],
[data-testid="stSkillsNudgeAnchor"],
[data-testid="stToastContainer"],
footer {{ display: none !important; }}

/* Keep the toolbar itself present but visually inert. */
[data-testid="stToolbar"] {{
  background: transparent !important;
  box-shadow: none !important;
  right: auto !important;
  left: 0 !important;
}}

/* Keep both sidebar controls visible and legible against the dark app bg. */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stExpandSidebarButton"],
[data-testid="stExpandSidebarButton"] button {{
  display: inline-flex !important;
  visibility: visible !important;
  opacity: 1 !important;
  color: var(--text-muted) !important;
}}
[data-testid="stSidebarCollapseButton"] button:hover,
[data-testid="stExpandSidebarButton"] button:hover {{
  color: var(--text) !important;
  background: var(--hover) !important;
}}
[data-testid="stSidebarHeader"] {{ padding-bottom: 0 !important; }}

/* --- Surfaces --- */
[data-testid="stAppViewContainer"] {{ background: var(--app-bg); }}
[data-testid="stSidebar"] {{
  background: var(--sidebar-bg);
  border-right: 1px solid var(--border);
}}
[data-testid="stSidebar"] > div {{ padding-top: 1.1rem; }}
[data-testid="stBottomBlockContainer"] {{ background: var(--app-bg); }}

body, .stMarkdown, p, li, span, label {{ color: var(--text); }}
h1, h2, h3, h4, h5, h6 {{ color: var(--text) !important; }}

/* Centre the conversation like ChatGPT rather than filling the width.
   The generous top padding clears the (now transparent but still
   space-occupying) header — without it the first message renders clipped
   underneath it. */
[data-testid="stMainBlockContainer"] {{
  max-width: 780px;
  padding-top: 4rem;
  padding-bottom: 5rem;
}}

/* --- Messages --- */
[data-testid="stChatMessage"] {{
  background: transparent;
  border: none;
  padding: 0.15rem 0 1.15rem 0;
  gap: 0.75rem;
}}
/* User turns read as a right-aligned bubble; assistant turns run flat on
   the background, which is the core visual rhythm of the ChatGPT UI. */
/* One consolidated rule for the user row. This had accumulated into three
   separate blocks for the same selector across successive fixes; merged so
   the layout can be read in one place.
     - row-reverse + flex-end pins the bubble to the right
     - the avatar is hidden, and the gutter Streamlit reserves for it is
       zeroed, or the bubble sits away from the edge
     - flex: 0 1 auto is load-bearing: Streamlit sets flex:1 on the content
       element, which otherwise stretches the bubble to full width and
       leaves a short prompt stranded at its left edge */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {{
  flex-direction: row-reverse;
  justify-content: flex-end !important;
  align-items: flex-start !important;
  gap: 0 !important;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageAvatarUser"] {{ display: none !important; }}

/* The bubble centres its own contents on both axes. Relying on padding
   alone left the text visibly off-centre, because Streamlit's own margins
   and line-height on the inner <p> shifted it; making the bubble a flex
   container removes that dependency entirely. Column direction so a
   multi-part message still stacks rather than running side by side. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {{
  background: var(--user-bubble);
  border-radius: 18px;
  padding: 0.6rem 1.05rem !important;
  flex: 0 1 auto !important;
  width: auto !important;
  max-width: 78%;
  margin-left: auto !important;
  margin-right: 0 !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  text-align: center !important;
}}
/* Applied to every descendant, not to individually-named wrappers.
   Streamlit nests message content several levels deep
   (stVerticalBlock > stElementContainer > stMarkdown > stMarkdownContainer
   > p) and that chain is version-dependent.

   Zeroing the vertical margins/padding/gap is what actually centres the
   text: Streamlit puts a bottom margin on those wrapper divs, which
   inflated the pill and left the text riding at the top with dead space
   beneath it. Only the <p> was being reset before, so the wrappers kept
   pushing it off-centre. With them flat, the pill's own padding is the
   sole vertical spacing and the flex centring holds.

   A user turn only ever holds plain text, so a blanket rule is safe here,
   and it is scoped to user turns — assistant answers, which do contain
   code blocks and lists, are untouched. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] * {{
  text-align: center !important;
  margin-top: 0 !important;
  margin-bottom: 0 !important;
  padding-top: 0 !important;
  padding-bottom: 0 !important;
  row-gap: 0 !important;
  gap: 0 !important;
  min-height: 0 !important;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] > * {{
  width: 100% !important;
}}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] p {{
  line-height: 1.45 !important;
}}

[data-testid="stChatMessageContent"] p {{ margin-bottom: 0.55rem; line-height: 1.65; }}
[data-testid="stChatMessageContent"] p:last-child {{ margin-bottom: 0; }}

/* --- Chat input: rounded pill, pinned bottom ---
   Streamlit draws its own inner border/outline on the chat input wrapper,
   which showed through as a broken or double edge. Those are cleared and
   a single solid border is drawn on the outer element instead. */
[data-testid="stChatInput"] {{
  background: var(--input-bg) !important;
  border: 1px solid var(--input-border) !important;
  border-radius: 26px !important;
  box-shadow: 0 2px 12px rgba(0,0,0,.10);
  overflow: hidden;
}}
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] > div > div,
[data-testid="stChatInputTextArea"] {{
  background: transparent !important;
  border: none !important;
  outline: none !important;
  box-shadow: none !important;
}}
[data-testid="stChatInput"]:focus-within {{
  border-color: var(--text-muted) !important;
}}
[data-testid="stChatInput"] textarea {{ color: var(--text) !important; }}
[data-testid="stChatInput"] textarea::placeholder {{ color: var(--text-muted) !important; }}
/* Both the bottom strip and its outer bar must match the app background,
   or the input appears to sit on a lighter panel with bright rectangles
   flanking it. */
[data-testid="stBottom"],
[data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"] {{
  background: var(--app-bg) !important;
  border-top: none !important;
  box-shadow: none !important;
}}
[data-testid="stBottomBlockContainer"] {{ padding-bottom: 1.1rem; }}
[data-testid="stChatInputInstructions"] {{ display: none !important; }}

/* --- Sidebar buttons: flat, left-aligned, subtle hover ---
   Targeted via data-testid with !important because Streamlit styles its
   buttons with emotion CSS-in-JS, whose specificity beats a plain
   `.stButton > button` rule (the first attempt here rendered centred,
   bordered buttons — caught by screenshotting the running app). */
[data-testid="stSidebar"] [data-testid^="stBaseButton-"] {{
  width: 100% !important;
  justify-content: flex-start !important;
  text-align: left !important;
  background: transparent !important;
  color: var(--text) !important;
  border: none !important;
  border-radius: 8px !important;
  padding: 0.45rem 0.6rem !important;
  font-size: 0.88rem !important;
  font-weight: 400 !important;
  min-height: 0 !important;
  box-shadow: none !important;
  transition: background .12s ease;
}}
/* The label lives in a nested markdown container that carries its own
   centring, so aligning only the <button> leaves the text centred. */
[data-testid="stSidebar"] [data-testid^="stBaseButton-"] > div,
[data-testid="stSidebar"] [data-testid^="stBaseButton-"] [data-testid="stMarkdownContainer"] {{
  width: 100% !important;
  text-align: left !important;
  justify-content: flex-start !important;
  display: block !important;
}}
[data-testid="stSidebar"] [data-testid^="stBaseButton-"] p {{
  text-align: left !important;
  font-size: 0.88rem !important;
  margin: 0 !important;
}}
[data-testid="stSidebar"] [data-testid^="stBaseButton-"]:hover {{
  background: var(--hover) !important;
}}
/* Active repo is rendered as type="primary" — using Streamlit's own
   button kind is far more robust than trying to match on label text. */
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {{
  background: var(--active) !important;
  font-weight: 500 !important;
}}
/* The one real action in the sidebar keeps a border so it reads as a
   button rather than a list row. */
[data-testid="stSidebar"] [data-testid^="stBaseButton-secondaryFormSubmit"] {{
  border: 1px solid var(--input-border) !important;
  justify-content: center !important;
  color: var(--text-muted) !important;
}}
[data-testid="stSidebar"] [data-testid^="stBaseButton-secondaryFormSubmit"] p,
[data-testid="stSidebar"] [data-testid^="stBaseButton-secondaryFormSubmit"] > div,
[data-testid="stSidebar"] [data-testid^="stBaseButton-secondaryFormSubmit"]
  [data-testid="stMarkdownContainer"] {{
  text-align: center !important;
}}

/* Tighten the repo list — Streamlit's default block gap makes a plain
   list of names read as separate widgets rather than one menu. Not tighter
   than this: at 0.15rem the section labels collapsed into the widget below. */
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.3rem; }}

/* Progress bar: pinned to the accent explicitly rather than relying on
   Streamlit resolving primaryColor from config, which did not hold. */
[data-testid="stSidebar"] [data-testid="stProgressBarTrack"] > div,
[data-testid="stSidebar"] [data-testid="stProgress"] div[role="progressbar"] > div {{
  background-color: var(--accent) !important;
  background-image: none !important;
}}
[data-testid="stSidebar"] [data-testid^="stBaseButton-secondaryFormSubmit"]:hover {{
  border-color: var(--text-muted) !important;
  color: var(--text) !important;
}}

[data-testid="stSidebar"] input {{
  background: var(--input-bg) !important;
  color: var(--text) !important;
  border: 1px solid var(--input-border) !important;
  border-radius: 8px !important;
  font-size: 0.86rem !important;
}}

/* --- Starter-prompt buttons in the empty state --- */
[data-testid="stMainBlockContainer"] [data-testid^="stBaseButton-"] {{
  background: transparent !important;
  color: var(--text) !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px !important;
  padding: 0.75rem 0.9rem !important;
  font-size: 0.86rem !important;
  font-weight: 400 !important;
  text-align: left !important;
  justify-content: flex-start !important;
  box-shadow: none !important;
  transition: background .12s ease;
}}
[data-testid="stMainBlockContainer"] [data-testid^="stBaseButton-"]:hover {{
  background: var(--hover) !important;
  border-color: var(--input-border) !important;
}}
[data-testid="stMainBlockContainer"] [data-testid^="stBaseButton-"] p {{
  text-align: left !important;
  font-size: 0.86rem !important;
  margin: 0 !important;
}}

/* --- Sources expander --- */
[data-testid="stExpander"] {{
  border: 1px solid var(--border);
  border-radius: 10px;
  background: transparent;
}}
[data-testid="stExpander"] summary {{ font-size: 0.85rem; color: var(--text-muted); }}
[data-testid="stExpander"] summary:hover {{ color: var(--text); }}

/* --- Small shared pieces --- */
/* Section labels are st.caption elements (see sidebar.py for why they are
   not hand-rolled divs). */
/* Section headings — the "ADD REPOSITORY" one is a text-input label, the
   "REPOSITORIES" one a caption. Both are styled identically so they read
   as one system. Layout (height, spacing) is left entirely to Streamlit;
   only colour and type are overridden. Overriding the box was what caused
   these to render clipped behind the widget below them. */
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] label {{
  color: var(--text-muted) !important;
  font-size: 0.68rem !important;
  font-weight: 600 !important;
  letter-spacing: .07em;
  text-transform: uppercase;
}}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
  margin-top: .6rem;
}}
.cite {{
  display: inline-block;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.76rem;
  color: var(--text-muted);
  background: var(--code-bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 1px 7px;
  margin: 0 4px 4px 0;
}}
.phase {{
  color: var(--text-muted);
  font-size: 0.85rem;
  display: flex;
  align-items: center;
  gap: .5rem;
}}
/* Which agent workflow produced the answer — visible evidence that the
   route/revision machinery is real, not decoration. */
.meta-row {{
  color: var(--text-muted);
  font-size: 0.72rem;
  margin-top: .5rem;
  letter-spacing: .02em;
}}
.dot {{
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.1s ease-in-out infinite;
}}
@keyframes pulse {{ 0%,100% {{ opacity:.25 }} 50% {{ opacity:1 }} }}

.empty-wrap {{ text-align:center; padding: 13vh 0 2rem; }}
.empty-wrap h1 {{ font-size: 1.8rem; font-weight: 600; margin-bottom: .45rem; }}
.empty-wrap p {{ color: var(--text-muted); font-size: .92rem; }}

.status-pill {{
  font-size: .68rem;
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 1px 7px;
}}
</style>
"""
