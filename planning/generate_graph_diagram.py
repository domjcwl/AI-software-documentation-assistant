"""Render planning/langgraph_flow.png from the *actual* compiled LangGraph.

The node and edge lists are read from `build_graph(...).get_graph()`, not
typed out by hand, so the diagram cannot drift from the code: if a node is
added or an edge rewired, re-running this reflects it. The script verifies
the structure it read matches what it is about to draw and fails loudly if
they disagree.

Rendered locally with Pillow. LangGraph's own `draw_mermaid_png()` posts the
graph to mermaid.ink and `draw_png()` needs pygraphviz — neither is wanted
here (no external service, no extra native dependency).

Regenerate:
    venv/Scripts/python.exe planning/generate_graph_diagram.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.graph import build_graph  # noqa: E402
from app.config import Settings  # noqa: E402

OUT = Path(__file__).parent / "langgraph_flow.png"

# --- palette (light, so it reads in both light and dark doc viewers) ---
BG = "#FFFFFF"
INK = "#1F2328"
MUTED = "#6E7781"
AGENT_FILL = "#E8F5F0"
AGENT_LINE = "#10A37F"
SUPPORT_FILL = "#EEF2F6"
SUPPORT_LINE = "#8C9BAB"
TERMINAL_FILL = "#2F363D"
EDGE = "#8C9BAB"
COND = "#D97706"
LOOP = "#DC2626"

W, H = 1160, 780
BOX_W, BOX_H = 216, 62
SCALE = 2  # supersample, then downscale — cheap antialiasing

# The four specialised agents from planning/architecture.md §4.
AGENTS = {"coordinator", "retrieval", "explanation", "review"}

# node id -> (centre x, centre y, subtitle)
LAYOUT = {
    "__start__": (520, 118, ""),
    "coordinator": (520, 208, "route + plan queries"),
    "clarify": (196, 330, "ask for detail"),
    "directory": (520, 330, "exact file tree"),
    "retrieval": (872, 330, "multi-query + fusion"),
    "explanation": (696, 456, "streams the answer"),
    "review": (696, 570, "grounding + citations"),
    "__end__": (520, 682, ""),
}

# Saves a reader having to open architecture.md to know what the routes
# mean. Placed top-left, above the coordinator's fan-out line — the space
# under `clarify` looks free but the clarify -> END edge runs through it.
ROUTE_NOTES = [
    ("code_qa", "how a specific piece of code works"),
    ("architecture", "high-level project overview"),
    ("directory", "file / folder layout"),
    ("modification", "where to change code for a feature"),
    ("clarify", "reference can't be resolved"),
]

LABELS = {"__start__": "START", "__end__": "END"}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    try:
        return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
    except OSError:
        return ImageFont.load_default()


def s(v: int | float) -> int:
    return int(v * SCALE)


def box(node: str) -> tuple[int, int, int, int]:
    """left, top, right, bottom for a node, in unscaled coords."""
    cx, cy, _ = LAYOUT[node]
    w, h = (150, 44) if node in ("__start__", "__end__") else (BOX_W, BOX_H)
    return cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2


def draw_node(d: ImageDraw.ImageDraw, node: str) -> None:
    left, top, right, bottom = box(node)
    terminal = node in ("__start__", "__end__")
    if terminal:
        fill, line, text_col = TERMINAL_FILL, TERMINAL_FILL, "#FFFFFF"
    elif node in AGENTS:
        fill, line, text_col = AGENT_FILL, AGENT_LINE, INK
    else:
        fill, line, text_col = SUPPORT_FILL, SUPPORT_LINE, INK

    d.rounded_rectangle(
        [s(left), s(top), s(right), s(bottom)],
        radius=s(22 if terminal else 12),
        fill=fill,
        outline=line,
        width=s(2),
    )

    label = LABELS.get(node, node)
    subtitle = LAYOUT[node][2]
    cx = (left + right) // 2
    if subtitle:
        d.text((s(cx), s(top + 18)), label, font=font(19, bold=True), fill=text_col, anchor="mm")
        d.text((s(cx), s(top + 42)), subtitle, font=font(14), fill=MUTED, anchor="mm")
    else:
        d.text(
            (s(cx), s((top + bottom) // 2)),
            label,
            font=font(17, bold=True),
            fill=text_col,
            anchor="mm",
        )


def arrow_head(d: ImageDraw.ImageDraw, x: int, y: int, direction: str, colour: str) -> None:
    a = 9
    if direction == "down":
        pts = [(x, y), (x - a, y - a * 1.4), (x + a, y - a * 1.4)]
    elif direction == "up":
        pts = [(x, y), (x - a, y + a * 1.4), (x + a, y + a * 1.4)]
    elif direction == "right":
        pts = [(x, y), (x - a * 1.4, y - a), (x - a * 1.4, y + a)]
    else:
        pts = [(x, y), (x + a * 1.4, y - a), (x + a * 1.4, y + a)]
    d.polygon([(s(px), s(py)) for px, py in pts], fill=colour)


def polyline(d: ImageDraw.ImageDraw, pts, colour: str, dashed: bool = False, width: int = 2) -> None:
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if not dashed:
            d.line([s(x1), s(y1), s(x2), s(y2)], fill=colour, width=s(width))
            continue
        length = max(abs(x2 - x1), abs(y2 - y1))
        if length == 0:
            continue
        steps = max(int(length / 12), 1)
        for i in range(steps):
            if i % 2:
                continue
            t0, t1 = i / steps, min((i + 0.55) / steps, 1.0)
            d.line(
                [
                    s(x1 + (x2 - x1) * t0),
                    s(y1 + (y2 - y1) * t0),
                    s(x1 + (x2 - x1) * t1),
                    s(y1 + (y2 - y1) * t1),
                ],
                fill=colour,
                width=s(width),
            )


def label(d: ImageDraw.ImageDraw, x: int, y: int, text: str, colour: str, bold: bool = False) -> None:
    f = font(14, bold=bold)
    box_ = d.textbbox((s(x), s(y)), text, font=f, anchor="mm")
    pad = s(5)
    d.rectangle(
        [box_[0] - pad, box_[1] - pad // 2, box_[2] + pad, box_[3] + pad // 2], fill=BG
    )
    d.text((s(x), s(y)), text, font=f, fill=colour, anchor="mm")


def verify_structure(graph_edges) -> None:
    """Fail loudly if the compiled graph no longer matches this layout,
    rather than silently drawing a stale picture."""
    drawn = set(LAYOUT)
    actual = {e.source for e in graph_edges} | {e.target for e in graph_edges}
    missing = actual - drawn
    extra = drawn - actual
    if missing or extra:
        raise SystemExit(
            f"Graph changed — update LAYOUT in this script.\n"
            f"  in graph but not drawn: {sorted(missing)}\n"
            f"  drawn but not in graph: {sorted(extra)}"
        )


def main() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-diagram")
    # Dependencies are only closed over by build_graph, never called here.
    graph = build_graph(settings, object(), object(), "owner/repo").get_graph()
    edges = list(graph.edges)
    verify_structure(edges)

    img = Image.new("RGB", (s(W), s(H)), BG)
    d = ImageDraw.Draw(img)

    d.text((s(56), s(40)), "LangGraph agent workflow", font=font(30, bold=True), fill=INK)
    d.text(
        (s(56), s(78)),
        "AI Software Documentation Assistant  ·  POST /chat  ·  generated from the compiled graph",
        font=font(15),
        fill=MUTED,
    )

    spine_x = LAYOUT["coordinator"][0]

    # --- edges, drawn before nodes so boxes sit on top of the lines ---
    polyline(d, [(spine_x, box("__start__")[3]), (spine_x, box("coordinator")[1] - 10)], EDGE)
    arrow_head(d, spine_x, box("coordinator")[1], "down", EDGE)

    # coordinator -> the three routes (conditional)
    fan_y = 278
    polyline(d, [(spine_x, box("coordinator")[3]), (spine_x, fan_y)], COND, dashed=True)
    for node_id, route_text in (
        ("clarify", "clarify"),
        ("directory", "directory"),
        ("retrieval", "code_qa · architecture · modification"),
    ):
        cx = LAYOUT[node_id][0]
        top = box(node_id)[1]
        polyline(d, [(spine_x, fan_y), (cx, fan_y), (cx, top - 10)], COND, dashed=True)
        arrow_head(d, cx, top, "down", COND)
        # Labels sit above the fan line rather than on it, so they don't
        # punch gaps through the edge they are annotating.
        d.text((s(cx), s(fan_y - 15)), route_text, font=font(13), fill=COND, anchor="mm")

    # retrieval / directory -> explanation
    exp_x, exp_top = LAYOUT["explanation"][0], box("explanation")[1]
    merge_y = 404
    for node_id in ("directory", "retrieval"):
        cx = LAYOUT[node_id][0]
        polyline(d, [(cx, box(node_id)[3]), (cx, merge_y), (exp_x, merge_y), (exp_x, exp_top - 10)], EDGE)
    arrow_head(d, exp_x, exp_top, "down", EDGE)

    # explanation -> review
    polyline(d, [(exp_x, box("explanation")[3]), (exp_x, box("review")[1] - 10)], EDGE)
    arrow_head(d, exp_x, box("review")[1], "down", EDGE)

    # review -> END (conditional, finalise)
    finalise_y = 640
    polyline(
        d,
        [(exp_x, box("review")[3]), (exp_x, finalise_y), (spine_x, finalise_y),
         (spine_x, box("__end__")[1] - 10)],
        COND,
        dashed=True,
    )
    arrow_head(d, spine_x, box("__end__")[1], "down", COND)
    d.text((s(608), s(finalise_y - 14)), "finalise", font=font(13), fill=COND, anchor="mm")

    # review -> retrieval (the bounded revision loop)
    loop_x = 1086
    review_y = LAYOUT["review"][1]
    retrieval_y = LAYOUT["retrieval"][1]
    polyline(
        d,
        [(box("review")[2], review_y), (loop_x, review_y), (loop_x, retrieval_y),
         (box("retrieval")[2] + 10, retrieval_y)],
        LOOP,
        dashed=True,
        width=3,
    )
    arrow_head(d, box("retrieval")[2], retrieval_y, "left", LOOP)
    # Beside the vertical run, not across it.
    d.text((s(loop_x - 12), s(440)), "revise", font=font(13, bold=True), fill=LOOP, anchor="rm")
    d.text((s(loop_x - 12), s(458)), "max 2", font=font(13), fill=LOOP, anchor="rm")

    # clarify -> END
    clarify_x = LAYOUT["clarify"][0]
    end_y = LAYOUT["__end__"][1]
    polyline(d, [(clarify_x, box("clarify")[3]), (clarify_x, end_y), (box("__end__")[0] - 10, end_y)], EDGE)
    arrow_head(d, box("__end__")[0], end_y, "right", EDGE)

    # --- route reference (top-left, clear of every edge) ---
    nx, ny = 56, 128
    d.text((s(nx), s(ny)), "ROUTES", font=font(12, bold=True), fill=MUTED)
    for i, (name, meaning) in enumerate(ROUTE_NOTES):
        y = ny + 26 + i * 22
        d.text((s(nx), s(y)), name, font=font(13, bold=True), fill=AGENT_LINE)
        d.text((s(nx + 92), s(y)), meaning, font=font(13), fill=MUTED)

    # --- nodes ---
    for node_id in LAYOUT:
        draw_node(d, node_id)

    # --- legend ---
    lx, ly = 56, 736
    d.rounded_rectangle(
        [s(lx - 14), s(ly - 22), s(lx + 1010), s(ly + 34)],
        radius=s(10),
        fill="#FAFBFC",
        outline="#E1E4E8",
        width=s(1),
    )
    d.rounded_rectangle(
        [s(lx), s(ly - 8), s(lx + 26), s(ly + 8)], radius=s(5),
        fill=AGENT_FILL, outline=AGENT_LINE, width=s(2),
    )
    d.text((s(lx + 36), s(ly)), "specialised agent (LLM call)", font=font(14), fill=INK, anchor="lm")
    d.rounded_rectangle(
        [s(lx + 250), s(ly - 8), s(lx + 276), s(ly + 8)], radius=s(5),
        fill=SUPPORT_FILL, outline=SUPPORT_LINE, width=s(2),
    )
    d.text((s(lx + 286), s(ly)), "no vector search", font=font(14), fill=INK, anchor="lm")
    polyline(d, [(lx + 430, ly), (lx + 476, ly)], EDGE)
    d.text((s(lx + 486), s(ly)), "always", font=font(14), fill=INK, anchor="lm")
    polyline(d, [(lx + 570, ly), (lx + 616, ly)], COND, dashed=True)
    d.text((s(lx + 626), s(ly)), "conditional", font=font(14), fill=INK, anchor="lm")
    polyline(d, [(lx + 740, ly), (lx + 786, ly)], LOOP, dashed=True, width=3)
    d.text((s(lx + 796), s(ly)), "revision loop", font=font(14), fill=INK, anchor="lm")

    img = img.resize((W, H), Image.LANCZOS)
    img.save(OUT)
    print(f"wrote {OUT}  ({OUT.stat().st_size:,} bytes)")
    print(f"nodes: {len(LAYOUT)}  edges: {len(edges)}")


if __name__ == "__main__":
    main()
