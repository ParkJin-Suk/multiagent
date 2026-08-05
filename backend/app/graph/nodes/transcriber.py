"""④ STT·화자분리 에이전트 + 화자 이름 붙이기."""
from __future__ import annotations

from pathlib import Path

from ...config import settings
from ...events import emit, log, node_status
from ...schemas import SpeakerMap
from ...tools import stt
from ...tools.llm import structured
from ..state import ClipState

NAME = "transcriber"

SYSTEM = """당신은 영상 편집 보조다. 화자분리 결과(SPEAKER_00 같은 익명 라벨)와 대사를 보고
각 화자에게 자막에 쓸 짧은 한국어 호칭을 붙인다.

규칙:
- 대사에서 실제 이름이 드러나면 그 이름을 쓴다.
- 아니면 역할로 부른다: 진행자, 게스트, 심사위원, 남자1, 여자2, 리포터 등.
- 호칭은 5자 이내.
- summary 에는 이 클립에서 무슨 일이 벌어지는지 3문장으로 적는다."""


async def transcriber(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    clip = state.get("clip", {})
    if not clip.get("audio"):
        node_status(NAME, "error", message="클립 오디오가 없습니다.")
        return {"errors": ["[transcriber] 오디오 없음"]}

    try:
        provider = cfg.get("stt_provider") or settings.stt_provider
        log(f"STT 시작 (provider={provider})", node=NAME)

        segments, language, warnings = await stt.transcribe(
            Path(clip["audio"]),
            provider=provider,
            language=cfg.get("source_language"),
            subtitle_segments=state.get("subtitle_segments"),
            subtitle_language=state.get("subtitle_language", ""),
            clip_start=float(clip.get("start", 0)),
            clip_end=float(clip.get("end", 0)) or None,
        )
        for w in warnings:
            log(w, level="warn", node=NAME)

        if not segments:
            node_status(NAME, "error", message="대사를 하나도 얻지 못했습니다.")
            return {"errors": ["[transcriber] 전사 결과 없음"]}

        duration = float(clip.get("duration") or 0)
        gaps = stt.find_gaps(segments, duration)
        speakers = sorted({s["speaker"] for s in segments})
        log(f"발화 {len(segments)}개 · 화자 {len(speakers)}명 · 빈 구간 {len(gaps)}곳", node=NAME)

        # 화자 이름 붙이기
        speaker_map: dict[str, str] = {}
        summary = ""
        try:
            preview = "\n".join(
                f"[{s['speaker']}] {s['start']:.1f}s: {s['text']}" for s in segments[:60]
            )
            result: SpeakerMap = await structured(
                SpeakerMap, SYSTEM,
                f"영상 제목: {state.get('source', {}).get('title')}\n\n[대사]\n{preview}",
                temperature=0.2,
            )
            speaker_map = {s.speaker_id: s.name for s in result.speakers}
            summary = result.summary
        except Exception as exc:  # noqa: BLE001
            log(f"화자 이름 지정 실패({exc}) — 라벨을 그대로 씁니다.", level="warn", node=NAME)

        # 화자가 1명뿐이면 호칭을 붙이지 않는 편이 깔끔하다
        if len(speakers) <= 1:
            speaker_map = {}

        emit("transcript", transcript=segments, language=language,
             speaker_map=speaker_map, summary=summary, gaps=gaps)
        node_status(NAME, "done", lines=len(segments), speakers=len(speakers),
                    language=language)
        return {
            "transcript": segments,
            "language": language,
            "speaker_map": speaker_map,
            "speaker_summary": summary,
            "gaps": gaps,
            "stt_warnings": warnings,
        }
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[transcriber] {exc}"]}
