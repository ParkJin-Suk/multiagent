"""하이라이트 구간 추출.

1순위: yt-dlp 가 넘겨주는 heatmap (유튜브 '가장 많이 다시 본 장면').
       구조: [{'start_time': s, 'end_time': s, 'value': 0~1}, ...] 약 100 버킷.
2순위: 오디오 RMS 에너지 (웃음·환호·큰 소리 구간이 솟는다).

두 경우 모두 '길이 L 짜리 슬라이딩 윈도우의 점수 합' 이 최대인 구간들을
겹치지 않게 골라 후보로 만든다.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from ..events import log
from . import media


# ─────────────────────────────────────────────────────────────────────
#  공통: (시각, 점수) 곡선 → 후보 구간
# ─────────────────────────────────────────────────────────────────────
def _windows(
    curve: list[tuple[float, float]],
    duration: float,
    clip_len: float,
    count: int,
    pad: float = 2.0,
) -> list[dict]:
    """curve = [(t, score)] 등간격. 겹치지 않는 상위 구간 count 개."""
    if not curve or duration <= 0:
        return []

    step = curve[1][0] - curve[0][0] if len(curve) > 1 else 1.0
    step = max(step, 0.1)
    win = max(1, int(round(clip_len / step)))

    # 누적합으로 윈도우 점수 계산
    prefix = [0.0]
    for _, v in curve:
        prefix.append(prefix[-1] + v)

    scored: list[tuple[float, int]] = []
    for i in range(0, max(1, len(curve) - win + 1)):
        scored.append(((prefix[i + win] - prefix[i]) / win, i))
    scored.sort(reverse=True)

    picked: list[dict] = []
    for score, i in scored:
        start = max(0.0, curve[i][0] - pad)
        end = min(duration, curve[min(i + win, len(curve) - 1)][0] + pad)
        if end - start < clip_len * 0.6:
            continue
        # 기존 후보와 50% 이상 겹치면 버린다
        if any(min(end, p["end"]) - max(start, p["start"]) > (end - start) * 0.5 for p in picked):
            continue
        picked.append({"start": round(start, 2), "end": round(end, 2), "score": round(score, 4)})
        if len(picked) >= count:
            break
    return picked


# ─────────────────────────────────────────────────────────────────────
#  1. heatmap
# ─────────────────────────────────────────────────────────────────────
def from_heatmap(heatmap: list[dict], duration: float, clip_len: float, count: int) -> list[dict]:
    curve = [
        (float(h.get("start_time", 0)), float(h.get("value", 0) or 0))
        for h in heatmap
        if h.get("start_time") is not None
    ]
    curve.sort()
    out = _windows(curve, duration, clip_len, count)
    for c in out:
        c["source"] = "heatmap"
        c["reason"] = f"다시 본 비율 상위 (평균 {c['score']:.2f})"
    return out


# ─────────────────────────────────────────────────────────────────────
#  2. 오디오 에너지
# ─────────────────────────────────────────────────────────────────────
async def from_audio(
    audio_path: Path, duration: float, clip_len: float, count: int
) -> list[dict]:
    rms = await media.audio_rms_profile(audio_path, window=1.0)
    if not rms:
        return []

    # 3초 이동평균으로 노이즈 제거
    smooth: list[float] = []
    for i in range(len(rms)):
        lo, hi = max(0, i - 1), min(len(rms), i + 2)
        smooth.append(sum(rms[lo:hi]) / (hi - lo))

    peak = max(smooth) or 1.0
    curve = [(float(i), v / peak) for i, v in enumerate(smooth)]
    out = _windows(curve, duration or len(rms), clip_len, count)
    for c in out:
        c["source"] = "audio"
        c["reason"] = f"오디오 에너지 상위 (정규화 {c['score']:.2f})"
    return out


# ─────────────────────────────────────────────────────────────────────
#  진입점
# ─────────────────────────────────────────────────────────────────────
async def find_candidates(
    *,
    heatmap: list[dict] | None,
    audio_path: Path | None,
    duration: float,
    clip_len: float | None = None,
    count: int | None = None,
    strategy: str | None = None,
) -> tuple[list[dict], list[dict], str]:
    """(후보목록, GUI용 곡선, 사용된 전략) 반환."""
    clip_len = clip_len or (settings.clip_min_seconds + settings.clip_max_seconds) / 2
    count = count or settings.candidate_count
    strategy = (strategy or settings.highlight_strategy).lower()

    use_heatmap = bool(heatmap) and strategy in ("auto", "heatmap")

    if use_heatmap:
        cands = from_heatmap(heatmap or [], duration, clip_len, count)
        if cands:
            curve = [
                {"t": round(float(h.get("start_time", 0)), 2),
                 "v": round(float(h.get("value", 0) or 0), 4)}
                for h in (heatmap or [])
            ]
            log(f"heatmap 기반 후보 {len(cands)}개 (곡선 {len(curve)}점)")
            return cands, curve, "heatmap"
        log("heatmap 이 있지만 후보를 못 만들었습니다 → 오디오 분석으로 전환", level="warn")

    if audio_path and strategy in ("auto", "audio", "heatmap"):
        if strategy == "heatmap":
            log("heatmap 이 없는 영상입니다 → 오디오 에너지로 대체", level="warn")
        cands = await from_audio(audio_path, duration, clip_len, count)
        if cands:
            rms = await media.audio_rms_profile(audio_path, window=max(1.0, duration / 200))
            peak = max(rms) if rms else 1.0
            gap = duration / max(1, len(rms))
            curve = [{"t": round(i * gap, 2), "v": round(v / (peak or 1), 4)}
                     for i, v in enumerate(rms)]
            log(f"오디오 기반 후보 {len(cands)}개")
            return cands, curve, "audio"

    # 최후: 영상 앞쪽 구간
    log("하이라이트 신호를 찾지 못해 영상 앞부분을 사용합니다.", level="warn")
    end = min(duration, clip_len)
    return (
        [{"start": 0.0, "end": round(end, 2), "score": 0.0,
          "source": "fallback", "reason": "신호 없음 — 영상 도입부"}],
        [],
        "fallback",
    )
