"""나레이션 TTS — edge-tts 기본.

edge-tts 는 무료이고 키가 필요 없다. 설치만 하면 바로 쓸 수 있다.
  pip install edge-tts

Typecast 는 선택이다. TTS_PROVIDER=typecast 로 두고 키를 넣으면 그쪽을 쓰고,
호출이 실패하면 edge-tts 로 자동 폴백한다.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..config import settings
from ..events import log

TYPECAST_TTS = "https://api.typecast.ai/v1/text-to-speech"
TYPECAST_VOICES = "https://api.typecast.ai/v2/voices"

# ISO 639-1 → Typecast 가 쓰는 ISO 639-3
LANG3 = {"ko": "kor", "en": "eng", "ja": "jpn", "zh": "cmn", "es": "spa", "de": "deu", "fr": "fra"}

# edge-tts 는 감정 프리셋이 없어서 rate/pitch/volume 조합으로 흉내낸다.
EMOTION_TUNING = {
    "happy":   {"rate": 8, "pitch": 15, "volume": 0},
    "excited": {"rate": 12, "pitch": 20, "volume": 5},
    "sad":     {"rate": -8, "pitch": -12, "volume": -5},
    "angry":   {"rate": 10, "pitch": -5, "volume": 10},
    "whisper": {"rate": -5, "pitch": -5, "volume": -35},
    "normal":  {"rate": 0, "pitch": 0, "volume": 0},
}

EDGE_INSTALL_HINT = (
    "edge-tts 가 설치되어 있지 않습니다.\n"
    "  pip install edge-tts\n"
    "  (또는 pip install -r backend/requirements.txt)\n"
    "설치 없이 진행하려면 .env 에서 TTS_PROVIDER=none 으로 두세요 "
    "— 나레이션이 무음 구간으로 들어갑니다."
)


def _require_edge():
    try:
        import edge_tts  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(EDGE_INSTALL_HINT) from exc
    return edge_tts


async def synthesize(
    text: str,
    out_path: Path,
    *,
    emotion: str = "",
    tempo: float = 1.0,
    voice_id: str = "",
) -> float:
    """나레이션 한 줄을 오디오로 만들고 길이(초)를 반환."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip()
    if not text:
        return 0.0

    provider = (settings.tts_provider or "edge").lower()

    if provider == "none":
        return await _silence(text, out_path)

    if provider == "typecast" and settings.typecast_api_key:
        if await _typecast(text, out_path, emotion, tempo, voice_id):
            return await duration(out_path)
        log("Typecast 실패 → edge-tts 로 폴백", level="warn")

    # 설치 문제는 조용히 넘기지 않는다 (무음 나레이션으로 끝나면 원인을 못 찾는다)
    _require_edge()

    if await _edge(text, out_path, emotion, tempo, voice_id):
        return await duration(out_path)

    log("edge-tts 합성에 실패해 해당 나레이션은 무음으로 들어갑니다.", level="error")
    return await _silence(text, out_path)


# ─────────────────────────────────────────────────────────────────────
#  edge-tts
# ─────────────────────────────────────────────────────────────────────
def _pct(v: int) -> str:
    return f"{v:+d}%"


async def _edge(
    text: str, out_path: Path, emotion: str, tempo: float, voice_id: str, retries: int = 2
) -> bool:
    edge_tts = _require_edge()

    tune = EMOTION_TUNING.get((emotion or "normal").lower(), EMOTION_TUNING["normal"])
    # tempo(빈 구간에 맞추려는 배속) + 기본 속도 + 감정 보정을 합산
    rate = _pct(int(round((tempo - 1) * 100)) + settings.edge_rate + tune["rate"])
    pitch = f"{settings.edge_pitch + tune['pitch']:+d}Hz"
    volume = _pct(settings.edge_volume + tune["volume"])
    voice = voice_id or settings.edge_voice

    mp3 = out_path.with_suffix(".mp3")
    for attempt in range(retries + 1):
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
            await comm.save(str(mp3))
            if mp3.exists() and mp3.stat().st_size > 1000:
                break
            raise RuntimeError("빈 오디오가 반환됐습니다.")
        except Exception as exc:  # noqa: BLE001
            if attempt >= retries:
                log(f"edge-tts 실패({type(exc).__name__}: {exc})", level="warn")
                return False
            await asyncio.sleep(0.6 * (attempt + 1))

    if mp3 == out_path:
        return True

    from . import media

    try:
        await media.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp3),
            "-ar", "48000", "-ac", "2", str(out_path),
        ], label="TTS 변환")
    except RuntimeError as exc:
        # 받은 오디오가 깨진 경우. 노드를 죽이지 말고 무음 폴백으로 넘긴다
        log(f"TTS 오디오 변환 실패: {str(exc).splitlines()[0]}", level="warn")
        return False
    finally:
        mp3.unlink(missing_ok=True)
    return out_path.exists() and out_path.stat().st_size > 1000


# ─────────────────────────────────────────────────────────────────────
#  Typecast (선택)
# ─────────────────────────────────────────────────────────────────────
# ssfm-v30 이 지원하는 프리셋. 그 외 값을 보내면 422 가 떨어진다.
TYPECAST_PRESETS = {"normal", "happy", "sad", "angry", "whisper", "toneup", "tonedown"}
# ssfm-v21 은 4종만 지원
TYPECAST_PRESETS_V21 = {"normal", "happy", "sad", "angry"}


def typecast_body(text: str, voice: str, emotion: str, tempo: float) -> dict:
    """Typecast /v1/text-to-speech 요청 바디. 스펙 범위를 넘지 않게 클램프한다."""
    allowed = TYPECAST_PRESETS_V21 if settings.typecast_model == "ssfm-v21" else TYPECAST_PRESETS
    preset = (emotion or settings.typecast_emotion or "normal").lower()
    if preset not in allowed:
        preset = "normal"
    return {
        "voice_id": voice,
        "text": text[:2000],
        "model": settings.typecast_model,
        "language": LANG3.get(settings.target_language, "kor"),
        "prompt": {
            "emotion_type": "preset",
            "emotion_preset": preset,
            "emotion_intensity": max(0.0, min(2.0, settings.typecast_emotion_intensity)),
        },
        "output": {
            "audio_format": "wav",
            "volume": 100,
            "audio_pitch": 0,
            "audio_tempo": round(max(0.5, min(2.0, tempo)), 2),
        },
    }


async def _typecast(
    text: str, out_path: Path, emotion: str, tempo: float, voice_id: str
) -> bool:
    voice = voice_id or settings.typecast_voice_id
    if not voice:
        log("TYPECAST_VOICE_ID 가 비어 있습니다. "
            "`python check_tts.py` 로 목록을 뽑아 tc_ 로 시작하는 id 를 .env 에 넣으세요.",
            level="error")
        return False
    if not voice.startswith(("tc_", "uc_")):
        log(f"TYPECAST_VOICE_ID 가 이상합니다: '{voice}' — "
            "Typecast 보이스 id 는 tc_(기본) 또는 uc_(커스텀) 로 시작합니다.", level="error")
        return False

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                TYPECAST_TTS,
                headers={"X-API-KEY": settings.typecast_api_key,
                         "Content-Type": "application/json"},
                json=typecast_body(text, voice, emotion, tempo),
            )
            if r.status_code >= 400:
                # 본문에 detail / message 가 들어 있어 원인 파악의 핵심이다
                log(f"Typecast {r.status_code}: {r.text[:400]}", level="error")
                return False
            if r.headers.get("content-type", "").startswith("application/json"):
                log(f"Typecast 응답이 오디오가 아닙니다: {r.text[:300]}", level="error")
                return False
            out_path.write_bytes(r.content)
        return out_path.stat().st_size > 1000
    except Exception as exc:  # noqa: BLE001
        log(f"Typecast 오류({type(exc).__name__}): {exc}", level="error")
        return False


# ─────────────────────────────────────────────────────────────────────
#  공통
# ─────────────────────────────────────────────────────────────────────
async def _silence(text: str, out_path: Path) -> float:
    """TTS 를 안 쓰거나 실패했을 때. 텍스트 길이에 비례한 무음."""
    dur = max(1.5, round(len(text) / 6.2, 1))
    from . import media

    await media.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{dur:.2f}", str(out_path),
    ], label="무음 생성")
    return dur


async def duration(path: Path) -> float:
    from . import media

    info = await media.probe(path)
    return round(info.get("duration", 0.0), 3)


async def list_voices() -> list[dict]:
    """웹 화면의 보이스 목록. 기본은 edge-tts 한국어 음성."""
    if (settings.tts_provider or "").lower() == "typecast" and settings.typecast_api_key:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    TYPECAST_VOICES, headers={"X-API-KEY": settings.typecast_api_key}
                )
                r.raise_for_status()
                data = r.json()
            items = data if isinstance(data, list) else data.get("voices", data.get("result", []))
            return [
                {"id": v.get("voice_id") or v.get("id"),
                 "name": v.get("voice_name") or v.get("name", ""),
                 "provider": "typecast"}
                for v in items
            ]
        except Exception as exc:  # noqa: BLE001
            log(f"Typecast 보이스 목록 실패: {exc}", level="warn")

    try:
        import edge_tts

        voices = await edge_tts.list_voices()
        out = [
            {"id": v["ShortName"],
             "name": f"{v['ShortName'].split('-')[-1].replace('Neural', '')} "
                     f"({'여성' if v.get('Gender') == 'Female' else '남성'})",
             "provider": "edge"}
            for v in voices if v["ShortName"].startswith("ko-KR")
        ]
        if out:
            return sorted(out, key=lambda x: x["id"])
    except Exception:  # noqa: BLE001
        pass

    # 오프라인이거나 미설치일 때의 기본 목록
    return [
        {"id": "ko-KR-InJoonNeural", "name": "InJoon (남성)", "provider": "edge"},
        {"id": "ko-KR-HyunsuMultilingualNeural", "name": "Hyunsu (남성)", "provider": "edge"},
        {"id": "ko-KR-SunHiNeural", "name": "SunHi (여성)", "provider": "edge"},
    ]
