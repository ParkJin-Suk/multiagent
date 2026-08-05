"""TTS 설정 점검 도구.

  cd backend && python check_tts.py

.env 를 그대로 읽어서
  1) 지금 어떤 provider 로 동작할지
  2) edge-tts 가 설치돼 있는지
  3) Typecast 키가 유효한지 / 쓸 수 있는 보이스 목록(voice_id)
  4) 실제로 한 문장을 합성해 tts_check.wav 로 저장
까지 확인한다. 실패하면 서버 로그보다 훨씬 자세한 원인을 그대로 보여준다.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.tools import tts  # noqa: E402

SAMPLE = "이 문장이 들리면 나레이션 음성이 정상으로 만들어진 겁니다."
OUT = Path(__file__).resolve().parent / "tts_check.wav"


def line(t: str = "") -> None:
    print(t, flush=True)


async def check_edge() -> bool:
    line("── edge-tts ────────────────────────────────────────────")
    try:
        import edge_tts
    except ImportError:
        line("  ✗ 설치되어 있지 않습니다.")
        line("      pip install edge-tts")
        line("    (미설치면 나레이션이 조용히 '무음 트랙'으로 대체됩니다)")
        return False
    line(f"  ✓ 설치됨 (edge_tts {getattr(edge_tts, '__version__', '?')})")
    line(f"    EDGE_VOICE = {settings.edge_voice}")
    try:
        voices = await edge_tts.list_voices()
        ko = [v["ShortName"] for v in voices if v["ShortName"].startswith("ko-KR")]
        line(f"  ✓ 한국어 음성 {len(ko)}개: {', '.join(ko[:6])}")
        if settings.edge_voice not in ko:
            line(f"  ! EDGE_VOICE '{settings.edge_voice}' 가 목록에 없습니다. 오타를 확인하세요.")
    except Exception as exc:  # noqa: BLE001
        line(f"  ! 보이스 목록 조회 실패: {type(exc).__name__}: {exc}")
        line("    (사내망/방화벽이 speech.platform.bing.com 을 막고 있을 수 있습니다)")
    return True


async def check_typecast() -> bool:
    line("── Typecast ────────────────────────────────────────────")
    if not settings.typecast_api_key:
        line("  - TYPECAST_API_KEY 가 비어 있습니다 (Typecast 를 안 쓰면 정상)")
        return False
    line(f"  키 앞자리: {settings.typecast_api_key[:8]}…")

    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(tts.TYPECAST_VOICES,
                            headers={"X-API-KEY": settings.typecast_api_key})
    except Exception as exc:  # noqa: BLE001
        line(f"  ✗ 연결 실패: {type(exc).__name__}: {exc}")
        return False

    if r.status_code >= 400:
        line(f"  ✗ 보이스 목록 {r.status_code}: {r.text[:300]}")
        if r.status_code in (401, 403):
            line("    → 키가 잘못됐거나 만료됐습니다.")
        return False

    data = r.json()
    items = data if isinstance(data, list) else data.get("voices", data.get("result", []))
    line(f"  ✓ 보이스 {len(items)}개 조회됨")

    def vid(v):
        return v.get("voice_id") or v.get("id") or ""

    ko = [v for v in items if "ko" in str(v).lower() or "kor" in str(v).lower()]
    for v in (ko or items)[:12]:
        line(f"      {vid(v):24s} {v.get('voice_name') or v.get('name', '')}")
    if len(ko or items) > 12:
        line(f"      … 외 {len(ko or items) - 12}개")

    current = settings.typecast_voice_id
    if not current:
        line("  ✗ TYPECAST_VOICE_ID 가 비어 있습니다.")
        line(f"    → 위 목록에서 하나 골라 .env 에 넣으세요. 예: TYPECAST_VOICE_ID={vid((ko or items)[0]) if (ko or items) else 'tc_xxxxx'}")
        return False
    if not current.startswith(("tc_", "uc_")):
        line(f"  ✗ TYPECAST_VOICE_ID='{current}' 형식이 이상합니다 (tc_ 또는 uc_ 로 시작해야 함)")
        return False
    if vid and current not in [vid(v) for v in items]:
        line(f"  ! TYPECAST_VOICE_ID='{current}' 가 계정 보이스 목록에 없습니다.")
    else:
        line(f"  ✓ TYPECAST_VOICE_ID = {current}")
    return True


async def main() -> int:
    line()
    line(f"TTS_PROVIDER = {settings.tts_provider}")
    line(f"TYPECAST_MODEL = {settings.typecast_model}")
    line()

    edge_ok = await check_edge()
    line()
    tc_ok = await check_typecast()
    line()

    provider = (settings.tts_provider or "edge").lower()
    if provider == "typecast" and not tc_ok:
        line("→ Typecast 로 설정돼 있지만 위 문제 때문에 edge-tts 로 폴백합니다.")
    if provider != "none" and not edge_ok and not (provider == "typecast" and tc_ok):
        line("→ 지금 상태로는 나레이션이 전부 무음으로 들어갑니다.")
        line()
        return 1

    line("── 실제 합성 테스트 ────────────────────────────────────")
    try:
        dur = await tts.synthesize(SAMPLE, OUT, emotion="normal")
    except Exception as exc:  # noqa: BLE001
        line(f"  ✗ {type(exc).__name__}: {exc}")
        return 1

    if not OUT.exists() or OUT.stat().st_size < 1000:
        line("  ✗ 파일이 만들어지지 않았습니다.")
        return 1

    kb = OUT.stat().st_size / 1024
    line(f"  파일: {OUT}  ({kb:.0f}KB, {dur}초)")

    # 파일 크기로는 무음을 못 걸러낸다(무압축 wav 는 무음이어도 크다). 실제 음량을 잰다.
    from app.tools import media

    rms = await media.audio_rms_profile(OUT, window=0.2)
    peak = max(rms) if rms else 0.0
    line(f"  최대 음량(RMS): {peak:.4f}")
    if peak < 0.002:
        line("  ✗ 사실상 무음입니다 — TTS 가 실패해 무음 트랙으로 대체된 상태입니다.")
        line("    위의 edge-tts / Typecast 경고를 먼저 해결하세요.")
        return 1
    line("  ✓ 소리가 담겨 있습니다. 재생해서 목소리를 확인하세요.")
    line()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
