"""그래프 공유 상태."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class ClipState(TypedDict, total=False):
    job_id: str
    config: dict[str, Any]

    # ① fetcher
    source: dict[str, Any]        # 메타 + 경로
    heatmap: list[dict[str, Any]]
    subtitle_segments: list[dict[str, Any]]
    subtitle_language: str

    # ② highlighter / ③ clip_gate
    candidates: list[dict[str, Any]]
    curve: list[dict[str, Any]]   # GUI 용 (t, v)
    strategy: str
    chosen: dict[str, Any]        # {start, end}
    clip: dict[str, Any]          # {path, url, duration, width, height, fps, audio}

    # ④ transcriber
    transcript: list[dict[str, Any]]
    language: str
    speaker_map: dict[str, str]
    speaker_summary: str
    gaps: list[dict[str, Any]]
    stt_warnings: list[str]

    # ⑤ translator
    translated: list[dict[str, Any]]   # transcript + ko

    # ⑥ scripter
    script: dict[str, Any]            # ClipScript

    # ⑦ voicer
    narration_slots: list[dict[str, Any]]
    timeline: dict[str, Any]          # {inserts: [[at, dur]], added: float}

    # ⑧ subtitler
    subtitle: dict[str, Any]          # {ass, srt, out_w, out_h, events}

    # ⑨ renderer
    render: dict[str, Any]            # {path, url, duration, size_mb, thumbnail_url}

    logs: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]


def new_state(job_id: str, config: dict[str, Any]) -> ClipState:
    return {
        "job_id": job_id,
        "config": config,
        "source": {},
        "heatmap": [],
        "subtitle_segments": [],
        "subtitle_language": "",
        "candidates": [],
        "curve": [],
        "strategy": "",
        "chosen": {},
        "clip": {},
        "transcript": [],
        "language": "",
        "speaker_map": {},
        "speaker_summary": "",
        "gaps": [],
        "stt_warnings": [],
        "translated": [],
        "script": {},
        "narration_slots": [],
        "timeline": {},
        "subtitle": {},
        "render": {},
        "logs": [],
        "errors": [],
    }


NODE_ORDER = [
    "fetcher",
    "highlighter",
    "clip_gate",
    "transcriber",
    "translator",
    "scripter",
    "voicer",
    "subtitler",
    "renderer",
]

NODE_LABELS = {
    "fetcher": "영상 확보",
    "highlighter": "하이라이트 추출",
    "clip_gate": "구간 확인",
    "transcriber": "STT·화자분리",
    "translator": "번역",
    "scripter": "나레이션·드립 작성",
    "voicer": "나레이션 음성",
    "subtitler": "자막 생성",
    "renderer": "최종 합성",
}

NODE_DESCS = {
    "fetcher": "yt-dlp 로 메타·히트맵·자막 확보",
    "highlighter": "가장 많이 다시 본 구간 산출",
    "clip_gate": "사람이 구간을 최종 선택",
    "transcriber": "화자별 대사 텍스트화",
    "translator": "말투 지정 번역",
    "scripter": "맥락 파악 후 나레이션·드립 삽입",
    "voicer": "Typecast TTS + 배치(덕킹/정지)",
    "subtitler": "대사·나레이션·드립 3종 ASS",
    "renderer": "믹싱·리프레임·자막 burn-in",
}
