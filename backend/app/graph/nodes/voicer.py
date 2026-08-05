"""⑦ 음성 삽입 에이전트 — 나레이션만 TTS 로 만들고 배치를 계산한다.

- 나레이션: Typecast(또는 edge) TTS 음성 생성
- 드립: 음성 없음 (자막만)
- 화자 대사: 원본 오디오 그대로

만든 음성이 빈 구간보다 길면 ① 살짝 빠르게 읽히거나 ② 프레임 정지로 전환한다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ...config import settings
from ...events import emit, log, node_status
from ...tools import render as render_tool
from ...tools import tts
from ..state import ClipState

NAME = "voicer"
SPEEDUP_LIMIT = 1.25   # 이 배속까지는 눌러 담고, 넘으면 프레임 정지


async def voicer(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    script = state.get("script", {})
    narrations = script.get("narrations", [])
    clip = state.get("clip", {})

    if not narrations:
        log("나레이션이 없어 음성 합성을 건너뜁니다.", node=NAME)
        node_status(NAME, "done", count=0)
        return {"narration_slots": [], "timeline": {"inserts": [], "added": 0.0}}

    workdir: Path = settings.output_path / state["job_id"] / "narration"
    workdir.mkdir(parents=True, exist_ok=True)
    gaps = state.get("gaps", [])
    mode = (cfg.get("narration_mode") or settings.narration_mode).lower()

    async def make(i: int, n: dict) -> render_tool.NarrationSlot:
        path = workdir / f"n{i:02d}.wav"
        gap = next((g for g in gaps if g["start"] <= n["start"] <= g["end"]), None)
        room = (gap["end"] - n["start"]) if gap else 0.0

        dur = await tts.synthesize(n["text"], path, emotion=n.get("emotion", ""))
        # 빈 구간보다 조금 길면 배속으로 눌러 담는다
        if room and dur > room and dur / room <= SPEEDUP_LIMIT:
            tempo = min(SPEEDUP_LIMIT, dur / room + 0.02)
            dur = await tts.synthesize(n["text"], path, emotion=n.get("emotion", ""), tempo=tempo)
            log(f"나레이션 {i + 1} 을 {tempo:.2f}배속으로 조정 ({dur:.1f}초)", node=NAME)

        return render_tool.NarrationSlot(
            index=i, at=float(n["start"]), duration=round(dur, 3),
            audio=path, text=n["text"],
        )

    try:
        slots = await asyncio.gather(*[make(i, n) for i, n in enumerate(narrations)])
        slots = [s for s in slots if s.duration > 0.2]

        timeline = render_tool.plan(list(slots), gaps, mode=mode)

        payload = [{
            "index": s.index, "at": s.at, "final_at": s.final_at,
            "duration": s.duration, "mode": s.mode, "gap": s.gap_len,
            "text": s.text, "audio": str(s.audio),
        } for s in sorted(slots, key=lambda x: x.at)]

        freezes = sum(1 for s in slots if s.mode == "freeze")
        emit("narration", slots=payload,
             timeline={"inserts": timeline.inserts, "added": round(timeline.added, 2)})
        log(f"나레이션 {len(payload)}개 생성 — 덕킹 {len(payload) - freezes} / "
            f"정지삽입 {freezes} (+{timeline.added:.1f}초)", node=NAME)
        node_status(NAME, "done", count=len(payload), freeze=freezes,
                    added=round(timeline.added, 1))
        return {
            "narration_slots": payload,
            "timeline": {"inserts": [list(x) for x in timeline.inserts],
                         "added": round(timeline.added, 3)},
        }
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[voicer] {exc}"]}
