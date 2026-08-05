"""⑨ 최종 합성 에이전트 — 정지삽입 → 덕킹 믹스 → 리프레임 → 자막 burn-in."""
from __future__ import annotations

from pathlib import Path

from ...config import settings
from ...events import emit, log, node_status
from ...tools import media
from ...tools import render as render_tool
from ..state import ClipState

NAME = "renderer"


async def renderer(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    clip = state.get("clip", {})
    sub = state.get("subtitle", {})

    if not clip.get("path") or not Path(clip["path"]).exists():
        node_status(NAME, "error", message="클립 파일이 없습니다.")
        return {"errors": ["[renderer] 클립 없음"]}

    workdir: Path = settings.output_path / state["job_id"]
    out_name = "final.mp4"

    slots = [
        render_tool.NarrationSlot(
            index=s["index"], at=s["at"], duration=s["duration"],
            audio=Path(s["audio"]), text=s["text"], mode=s["mode"],
            final_at=s["final_at"], gap_len=s.get("gap", 0.0),
        )
        for s in state.get("narration_slots", [])
        if Path(s["audio"]).exists()
    ]

    try:
        spec = {
            "width": int(clip.get("width") or 1280),
            "height": int(clip.get("height") or 720),
            "fps": int(round(float(clip.get("fps") or 30))) or 30,
            "duration": float(clip.get("duration") or 0),
        }

        base = Path(clip["path"])
        if any(s.mode == "freeze" for s in slots):
            log("프레임 정지 구간 삽입 중…", node=NAME)
            base = await render_tool.insert_freezes(base, slots, workdir, spec)

        log("믹싱 · 리프레임 · 자막 burn-in 중…", node=NAME)
        out = await render_tool.finalize(
            base, slots,
            Path(sub["ass"]) if sub.get("ass") else None,
            workdir / out_name,
            reframe=bool(sub.get("reframe", settings.vertical_reframe)),
            out_w=int(sub.get("out_w") or settings.video_width),
            out_h=int(sub.get("out_h") or settings.video_height),
            duck_level=float(cfg.get("duck_level") or settings.duck_level),
        )

        info = await media.probe(out)
        thumb = workdir / "thumbnail.jpg"
        await render_tool.thumbnail(out, min(2.0, info.get("duration", 2) / 3), thumb)

        payload = {
            "path": str(out),
            "url": f"/api/artifacts/{state['job_id']}/{out_name}",
            "thumbnail_url": f"/api/artifacts/{state['job_id']}/thumbnail.jpg",
            "srt_url": sub.get("srt_url"),
            "ass_url": sub.get("ass_url"),
            "duration": round(info.get("duration", 0), 2),
            "size_mb": round(info.get("size", 0) / 1024 / 1024, 2),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
        }
        emit("render", render=payload)
        log(f"완성: {payload['duration']}초 / {payload['size_mb']}MB / "
            f"{payload['width']}x{payload['height']}", node=NAME)
        node_status(NAME, "done", duration=payload["duration"], size_mb=payload["size_mb"])
        return {"render": payload}
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[renderer] {exc}"]}
