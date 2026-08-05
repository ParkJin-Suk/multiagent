"""LangGraph 그래프 조립.

fetcher → highlighter → clip_gate(사람이 구간 선택) → transcriber
        → translator → scripter → voicer → subtitler → renderer → END

선형 파이프라인이다. 사람이 개입하는 곳은 clip_gate 한 곳뿐이고,
REVIEW_CLIP_SELECTION=false 로 두면 그마저도 자동으로 지나간다.
"""
from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from .nodes.clip_gate import clip_gate
from .nodes.fetcher import fetcher
from .nodes.highlighter import highlighter
from .nodes.renderer import renderer
from .nodes.scripter import scripter
from .nodes.subtitler import subtitler
from .nodes.transcriber import transcriber
from .nodes.translator import translator
from .nodes.voicer import voicer
from .state import ClipState

CHECKPOINTER = InMemorySaver()

CHAIN = [
    ("fetcher", fetcher),
    ("highlighter", highlighter),
    ("clip_gate", clip_gate),
    ("transcriber", transcriber),
    ("translator", translator),
    ("scripter", scripter),
    ("voicer", voicer),
    ("subtitler", subtitler),
    ("renderer", renderer),
]


def build_graph():
    g = StateGraph(ClipState)
    for name, fn in CHAIN:
        g.add_node(name, fn)

    g.add_edge(START, CHAIN[0][0])
    for (a, _), (b, _) in zip(CHAIN, CHAIN[1:]):
        g.add_edge(a, b)
    g.add_edge(CHAIN[-1][0], END)

    return g.compile(checkpointer=CHECKPOINTER)


@lru_cache
def get_graph():
    return build_graph()


def graph_mermaid() -> str:
    try:
        return get_graph().get_graph().draw_mermaid()
    except Exception:  # noqa: BLE001
        names = [n for n, _ in CHAIN]
        return "graph TD\n  " + " --> ".join(names) + " --> END\n"
