"""STT + 화자분리.

provider:
  whisperx       — faster-whisper 전사 → wav2vec 강제정렬 → pyannote 화자분리
  faster-whisper — 전사만 (화자분리 없음)
  subtitles      — 유튜브 자막 재활용 (설치 0, 테스트/오프라인용)

세 경우 모두 [{index,start,end,speaker,text}] 형태로 통일해서 돌려준다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import settings
from ..events import log


# ─────────────────────────────────────────────────────────────────────
#  WhisperX
# ─────────────────────────────────────────────────────────────────────
def _pick_device() -> tuple[str, str]:
    device = settings.whisper_device
    compute = settings.whisper_compute_type
    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    if device == "cpu" and compute in ("float16", "int8_float16"):
        compute = "int8"
    return device, compute


def _whisperx_sync(audio_path: str, language: str | None) -> dict:
    import whisperx

    device, compute = _pick_device()
    model = whisperx.load_model(
        settings.whisper_model, device, compute_type=compute,
        language=None if language in (None, "", "auto") else language,
    )
    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=8)
    lang = result.get("language", language or "en")

    # 1) 단어 단위 강제정렬
    try:
        align_model, meta = whisperx.load_align_model(language_code=lang, device=device)
        result = whisperx.align(
            result["segments"], align_model, meta, audio, device,
            return_char_alignments=False,
        )
        result["language"] = lang
    except Exception as exc:  # noqa: BLE001
        result.setdefault("language", lang)
        result["_align_error"] = str(exc)

    # 2) 화자분리
    if settings.hf_token:
        try:
            from whisperx.diarize import DiarizationPipeline

            diarizer = DiarizationPipeline(use_auth_token=settings.hf_token, device=device)
            kwargs = {}
            if settings.min_speakers:
                kwargs["min_speakers"] = settings.min_speakers
            if settings.max_speakers:
                kwargs["max_speakers"] = settings.max_speakers
            diarize_segments = diarizer(audio, **kwargs)
            result = whisperx.assign_word_speakers(diarize_segments, result)
        except Exception as exc:  # noqa: BLE001
            result["_diarize_error"] = str(exc)
    else:
        result["_diarize_error"] = "HF_TOKEN 이 없어 화자분리를 건너뜁니다."

    return result


async def _whisperx(audio_path: Path, language: str | None) -> tuple[list[dict], str, list[str]]:
    warnings: list[str] = []
    result = await asyncio.to_thread(_whisperx_sync, str(audio_path), language)

    if result.get("_align_error"):
        warnings.append(f"강제정렬 실패: {result['_align_error']}")
    if result.get("_diarize_error"):
        warnings.append(f"화자분리 없음: {result['_diarize_error']}")

    segments: list[dict] = []
    for i, seg in enumerate(result.get("segments", [])):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append({
            "index": i,
            "start": round(float(seg.get("start", 0)), 3),
            "end": round(float(seg.get("end", 0)), 3),
            "speaker": seg.get("speaker") or "SPEAKER_00",
            "text": text,
            "kind": "speech",
        })
    return segments, result.get("language", language or ""), warnings


# ─────────────────────────────────────────────────────────────────────
#  faster-whisper (화자분리 없음)
# ─────────────────────────────────────────────────────────────────────
def _faster_sync(audio_path: str, language: str | None) -> tuple[list[dict], str]:
    from faster_whisper import WhisperModel

    device, compute = _pick_device()
    model = WhisperModel(settings.whisper_model, device=device, compute_type=compute)
    segs, info = model.transcribe(
        audio_path, vad_filter=True,
        language=None if language in (None, "", "auto") else language,
    )
    out = []
    for i, s in enumerate(segs):
        text = (s.text or "").strip()
        if text:
            out.append({
                "index": i, "start": round(s.start, 3), "end": round(s.end, 3),
                "speaker": "SPEAKER_00", "text": text, "kind": "speech",
            })
    return out, info.language


# ─────────────────────────────────────────────────────────────────────
#  진입점
# ─────────────────────────────────────────────────────────────────────
async def transcribe(
    audio_path: Path,
    *,
    provider: str | None = None,
    language: str | None = None,
    subtitle_segments: list[dict] | None = None,
    subtitle_language: str = "",
    clip_start: float = 0.0,
    clip_end: float | None = None,
) -> tuple[list[dict], str, list[str]]:
    """(세그먼트, 감지언어, 경고목록)"""
    provider = (provider or settings.stt_provider).lower()
    language = language or settings.source_language

    if provider == "subtitles":
        segs = _slice_subtitles(subtitle_segments or [], clip_start, clip_end)
        if segs:
            return segs, subtitle_language or language or "", [
                "유튜브 자막을 사용해 화자분리가 없습니다 (전부 SPEAKER_00)."
            ]
        log("유튜브 자막이 없어 faster-whisper 로 전환합니다.", level="warn")
        provider = "faster-whisper"

    if provider == "whisperx":
        try:
            return await _whisperx(audio_path, language)
        except ImportError as exc:
            log(f"whisperx 미설치({exc}) → faster-whisper 시도", level="warn")
            provider = "faster-whisper"

    if provider == "faster-whisper":
        try:
            segs, lang = await asyncio.to_thread(_faster_sync, str(audio_path), language)
            return segs, lang, ["faster-whisper 라 화자분리가 없습니다 (전부 SPEAKER_00)."]
        except ImportError as exc:
            raise RuntimeError(
                f"STT 백엔드를 쓸 수 없습니다({exc}).\n"
                "  pip install -r backend/requirements-stt.txt 로 설치하거나,\n"
                "  .env 에서 STT_PROVIDER=subtitles 로 두고 자막이 있는 영상을 쓰세요."
            ) from exc

    raise RuntimeError(f"알 수 없는 STT_PROVIDER: {provider}")


def _slice_subtitles(
    segments: list[dict], clip_start: float, clip_end: float | None
) -> list[dict]:
    """전체 영상 기준 자막을 클립 기준 상대시간으로 자른다."""
    end = clip_end if clip_end is not None else float("inf")
    out: list[dict] = []
    for s in segments:
        if s["end"] <= clip_start or s["start"] >= end:
            continue
        out.append({
            "index": len(out),
            "start": round(max(0.0, s["start"] - clip_start), 3),
            "end": round(min(end, s["end"]) - clip_start, 3),
            "speaker": "SPEAKER_00",
            "text": s["text"].strip(),
            "kind": "speech",
        })
    return out


def find_gaps(segments: list[dict], duration: float, min_gap: float = 1.2) -> list[dict]:
    """대사가 없는 빈 구간. 나레이션을 끼워 넣을 자리 후보."""
    gaps: list[dict] = []
    cursor = 0.0
    for s in sorted(segments, key=lambda x: x["start"]):
        if s["start"] - cursor >= min_gap:
            gaps.append({"start": round(cursor, 2), "end": round(s["start"], 2),
                         "length": round(s["start"] - cursor, 2)})
        cursor = max(cursor, s["end"])
    if duration - cursor >= min_gap:
        gaps.append({"start": round(cursor, 2), "end": round(duration, 2),
                     "length": round(duration - cursor, 2)})
    return gaps
