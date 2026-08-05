"""⑥ 스크립트 에이전트 — 맥락을 읽고 나레이션과 드립을 꽂는다.

나레이션은 '음성으로 들어가는 해설' 이라 반드시 대사 없는 빈 구간에 넣어야 한다.
그래서 LLM 에게 실제 빈 구간 목록을 주고 그 안에서만 고르게 한다.
드립은 자막만 뜨므로 아무 데나 넣어도 된다.
"""
from __future__ import annotations

from ...config import settings
from ...events import emit, log, node_status
from ...schemas import ClipScript
from ...tools.llm import structured
from ..state import ClipState

NAME = "scripter"

SYSTEM = """당신은 해외 영상을 한국 시청자용 클립으로 재가공하는 편집자다.
번역된 대사 타임라인을 읽고 두 가지를 만든다.

1) 나레이션 (narrations) — TTS 로 음성이 들어간다.
   - 반드시 '사용 가능한 빈 구간' 안에서만 시작 시각을 잡는다. 대사 위에 겹치면 안 된다.
   - 빈 구간 길이보다 긴 문장을 쓰지 마라. 대략 1초에 6글자로 계산해라.
   - 용도는 셋 중 하나다: ① 상황 설명 ② 모르는 배경지식 보충 ③ 다음 장면 기대감 조성.
   - 웃기려고 하지 마라. 웃음은 드립이 담당한다. 나레이션은 담백하게.
   - 최대 개수를 넘기지 마라. 적으면 적을수록 좋다.

2) 라벨·드립 (gags) — 영상 위쪽에 뜨는 짧은 자막이다. 음성 없음.
   두 가지 용도로 쓴다.
   - 라벨: 등장인물이나 상황을 한 단어로 찍어준다. 괄호를 쓴다. 예: (가위) (보자기) (심판)
     인물이 바뀌거나 새 상황이 시작될 때 넣으면 이해가 확 쉬워진다.
   - 리액션 드립: 상황에 대한 짧은 한마디. 10자 이내.
   - 인신공격, 외모 비하, 정치·종교 조롱, 특정 집단 비하는 절대 금지.
   - gag_level 이 0이면 하나도 만들지 마라.

3) overlay_title — 영상 처음부터 끝까지 상단에 박아둘 컨셉 한 줄.
   - 14자 내외. 이 영상이 '무슨 상황인지' 한 방에 알려주는 문장.
   - 좋은 예: "가위바위보가 사람이 된다면", "면접관이 솔직해진다면"
   - 나쁜 예: 제목처럼 낚시성으로 쓰거나("충격 결말!"), 스포일러를 넣는 것.

마지막으로 유튜브 제목·설명·태그를 짓는다. 제목은 40자 이내, 낚시성 과장 금지.
모든 텍스트는 한국어."""


async def scripter(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    lines = state.get("translated") or state.get("transcript", [])
    if not lines:
        node_status(NAME, "skipped", message="대사 없음")
        return {"script": {}}

    clip = state.get("clip", {})
    duration = float(clip.get("duration") or 0)
    gaps = [g for g in state.get("gaps", []) if g["length"] >= 1.2]
    speaker_map = state.get("speaker_map", {})

    gag_level = int(cfg.get("gag_level", 2))
    max_nar = int(cfg.get("max_narrations") or settings.max_narrations)
    max_gag = 0 if gag_level == 0 else min(
        int(cfg.get("max_gags") or settings.max_gags), gag_level * 3
    )
    persona = (cfg.get("narration_persona") or "").strip()

    timeline = "\n".join(
        f"{l['start']:.1f}~{l['end']:.1f}s [{speaker_map.get(l['speaker'], l['speaker'])}] "
        f"{l.get('ko') or l['text']}"
        for l in lines
    )
    gap_text = "\n".join(
        f"- {g['start']:.1f}~{g['end']:.1f}s (길이 {g['length']:.1f}초 → 최대 "
        f"{int(g['length'] * 6)}자)"
        for g in gaps
    ) or "(빈 구간이 없습니다. narrations 를 비워두세요.)"

    user = f"""[영상] {state.get('source', {}).get('title')}
[클립 길이] {duration:.1f}초
[상황 요약] {state.get('speaker_summary') or '(없음)'}

[나레이터 캐릭터]
{persona or '차분하고 담백한 해설자. 과한 리액션 없음.'}

[사용 가능한 빈 구간 — 나레이션은 여기서만]
{gap_text}

[대사 타임라인 (번역본)]
{timeline}

[제약]
- 나레이션 최대 {max_nar}개
- 드립 최대 {max_gag}개 (gag_level={gag_level})

나레이션과 드립을 설계하라."""

    try:
        script: ClipScript = await structured(
            ClipScript, SYSTEM, user, model=settings.writer_model, temperature=0.8,
        )

        # 빈 구간 밖으로 튀어나온 나레이션은 가장 가까운 빈 구간으로 스냅
        narrations = _snap(script.narrations, gaps, duration)[:max_nar]
        gags = [g for g in script.gags if 0 <= g.start < duration][:max_gag]

        data = script.model_dump()
        data["narrations"] = [n.model_dump() if hasattr(n, "model_dump") else n
                              for n in narrations]
        data["gags"] = [g.model_dump() if hasattr(g, "model_dump") else g for g in gags]

        emit("script", script=data)
        log(f"나레이션 {len(data['narrations'])}개 · 드립 {len(data['gags'])}개", node=NAME)
        node_status(NAME, "done", narrations=len(data["narrations"]), gags=len(data["gags"]))
        return {"script": data}
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[scripter] {exc}"]}


def _snap(narrations, gaps: list[dict], duration: float):
    """나레이션 시작 시각을 실제 빈 구간 안으로 밀어 넣는다."""
    if not gaps:
        return []
    out = []
    used: list[float] = []
    for n in sorted(narrations, key=lambda x: x.start):
        start = min(max(0.0, n.start), max(0.0, duration - 0.5))
        inside = next((g for g in gaps if g["start"] <= start <= g["end"]), None)
        if inside is None:
            inside = min(gaps, key=lambda g: abs(g["start"] - start))
            start = inside["start"] + 0.15
        if any(abs(start - u) < 1.0 for u in used):
            continue
        used.append(start)
        n.start = round(start, 2)
        out.append(n)
    return out
