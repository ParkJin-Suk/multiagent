"""③ 구간 확정 게이트 — 사람이 구간을 고른 뒤 그 부분만 잘라 온다.

REVIEW_CLIP_SELECTION 이 켜져 있으면 여기서 그래프가 멈추고(interrupt)
웹 화면의 히트맵 그래프에서 사람이 후보를 고르거나 시각을 직접 입력한다.
"""
from __future__ import annotations

from pathlib import Path

from langgraph.types import interrupt

from ...config import settings
from ...events import emit, log, node_status
from ...tools import media, source
from ..state import ClipState

NAME = "clip_gate"


def _use_section_download(duration: float) -> bool:
    """구간만 받을지, 전체를 받고 로컬에서 자를지.

    유튜브는 구간 다운로드(`download_ranges`)를 걸면 yt-dlp 가 네이티브 다운로더 대신
    ffmpeg 다운로더를 강제로 쓴다. 네이티브 다운로더는 googlevideo 를 10MiB 씩
    Range 로 끊어 받아 속도 제한을 피하는데, ffmpeg 는 그 힌트를 무시하고 한 번에
    스트리밍하기 때문에 유튜브 쪽 스로틀링에 그대로 걸려 몇 배 느려진다.
    그래서 어지간한 길이면 통째로 받아서 로컬에서 자르는 쪽이 훨씬 빠르다.
    """
    if settings.download_sections_only:
        log("DOWNLOAD_SECTIONS_ONLY=true — 구간만 받습니다 "
            "(유튜브에서는 스로틀링으로 느릴 수 있습니다).", level="warn", node=NAME)
        return True

    limit = settings.section_download_over_minutes * 60
    if duration and duration > limit:
        log(f"영상이 {duration / 60:.0f}분으로 길어 "
            f"({settings.section_download_over_minutes:.0f}분 초과) 구간만 받습니다.",
            level="warn", node=NAME)
        return True

    log("전체를 받아 로컬에서 정확히 자릅니다 (유튜브 구간 다운로드보다 빠릅니다).",
        node=NAME)
    return False


async def clip_gate(state: ClipState) -> dict:
    cfg = state.get("config", {})
    cands = state.get("candidates", [])
    chosen = dict(state.get("chosen", {}))

    if not cands and not chosen:
        node_status(NAME, "error", message="후보 구간이 없습니다.")
        return {"errors": ["[clip_gate] 후보 없음"]}

    require = cfg.get("review_clip_selection")
    if require is None:
        require = settings.review_clip_selection

    if require and state.get("strategy") != "manual":
        node_status(NAME, "waiting")
        emit("clip_selection_required", candidates=cands, curve=state.get("curve", []),
             chosen=chosen, source=state.get("source", {}))
        log("구간 확인 대기 중 — 히트맵에서 구간을 고르고 확정하세요.", level="warn", node=NAME)

        decision = interrupt({
            "type": "clip_selection",
            "candidates": cands,
            "chosen": chosen,
        }) or {}

        if decision.get("cancel"):
            node_status(NAME, "error", message="사용자가 취소했습니다.")
            return {"errors": ["[clip_gate] 사용자 취소"]}

        if decision.get("start") is not None and decision.get("end") is not None:
            chosen = {"start": float(decision["start"]), "end": float(decision["end"]),
                      "index": -1}
        elif decision.get("candidate_index") is not None:
            idx = max(0, min(int(decision["candidate_index"]), len(cands) - 1))
            chosen = {"start": cands[idx]["start"], "end": cands[idx]["end"], "index": idx}

    node_status(NAME, "running")
    src = state.get("source", {})
    workdir: Path = settings.output_path / state["job_id"]
    start, end = float(chosen["start"]), float(chosen["end"])

    # 길이 상한 적용
    max_len = float(cfg.get("clip_seconds") or 0) or settings.clip_max_seconds
    if end - start > max_len:
        end = start + max_len
        chosen["end"] = round(end, 2)
        log(f"구간이 상한({max_len:.0f}초)을 넘어 잘랐습니다.", level="warn", node=NAME)

    try:
        url = cfg["url"]
        local = src.get("local_path")
        if local and Path(local).exists():
            clip_path = await media.cut(Path(local), workdir / "clip.mp4", start, end)
        elif _use_section_download(float(src.get("duration") or 0)):
            clip_path = await source.download(url, workdir, start, end)
        else:
            full = await source.download(url, workdir)
            clip_path = await media.cut(full, workdir / "clip.mp4", start, end)

        info = await media.probe(clip_path)
        audio = await media.extract_audio(clip_path, workdir / "clip_audio.wav")

        clip = {
            "path": str(clip_path),
            "audio": str(audio),
            "url": f"/api/artifacts/{state['job_id']}/{Path(clip_path).name}",
            "duration": round(info.get("duration", end - start), 2),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
            "fps": info.get("fps", 30) or 30,
            "start": round(start, 2),
            "end": round(end, 2),
        }
        emit("clip", clip=clip, chosen=chosen)
        log(f"클립 확보: {clip['duration']}초 / {clip['width']}x{clip['height']}", node=NAME)
        node_status(NAME, "done", duration=clip["duration"],
                    range=f"{start:.0f}~{end:.0f}s")
        return {"chosen": chosen, "clip": clip}
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[clip_gate] {exc}"]}
