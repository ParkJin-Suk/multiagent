"""⑧ 자막 에이전트 — 화자 대사 / 나레이션 / 드립을 3종 스타일 ASS 로 만든다.

프레임 정지가 삽입되면 최종 영상의 시간축이 밀리므로, 모든 자막 시각을
'원본 클립 시간 → 최종 영상 시간' 으로 변환한 뒤 찍는다.
"""
from __future__ import annotations

from pathlib import Path

from ...config import settings
from ...events import emit, log, node_status
from ...tools import subtitle as sub_tool
from ...tools.fonts import font_family, font_warning
from ..state import ClipState

NAME = "subtitler"


def _mapper(inserts: list) -> callable:
    pairs = [(float(a), float(d)) for a, d in (inserts or [])]

    def m(t: float) -> float:
        return round(t + sum(d for at, d in pairs if at <= t + 1e-6), 3)

    return m


async def subtitler(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    clip = state.get("clip", {})
    lines = state.get("translated") or state.get("transcript", [])
    script = state.get("script", {})
    slots = state.get("narration_slots", [])

    try:
        warn = font_warning()
        if warn:
            log(warn, level="warn", node=NAME)

        reframe = cfg.get("vertical_reframe")
        if reframe is None:
            reframe = settings.vertical_reframe

        if reframe:
            out_w, out_h = settings.video_width, settings.video_height
        else:
            out_w = int(clip.get("width") or settings.video_width) // 2 * 2
            out_h = int(clip.get("height") or settings.video_height) // 2 * 2

        m = _mapper(state.get("timeline", {}).get("inserts", []))

        # 대사는 통째로 띄우지 않고 2~3어절씩 끊어 여러 자막으로 나눈다
        dialogue: list[dict] = []
        for l in lines:
            text = (l.get("ko") or l.get("text", "")).strip()
            if not text:
                continue
            s, e = m(l["start"]), max(m(l["end"]), m(l["start"]) + 0.7)
            for piece in sub_tool.chunk_line(text, s, e):
                dialogue.append({**piece, "speaker": l.get("speaker", "")})
        narration_subs = [
            {"start": s["final_at"], "end": s["final_at"] + s["duration"] + 0.2,
             "text": s["text"]}
            for s in slots
        ]
        gag_subs = [
            {"start": m(g["start"]), "end": m(g["start"]) + float(g.get("duration") or 1.8),
             "text": g["text"]}
            for g in script.get("gags", [])
        ]

        # 자막 크기는 세로 리프레임 여부에 맞춰 보정
        size = settings.subtitle_size
        if not reframe:
            size = max(28, round(settings.subtitle_size * out_h / settings.video_height * 1.6))

        # 최종 프레임에서 '실제 영상'이 차지하는 세로 범위 (검은 레터박스 제외)
        cw = int(clip.get("width") or out_w) or out_w
        ch = int(clip.get("height") or out_h) or out_h
        if reframe:
            fitted_h = min(out_h, round(out_w * ch / cw))
            video_top = (out_h - fitted_h) / 2
            video_bottom = video_top + fitted_h
        else:
            video_top, video_bottom = 0.0, float(out_h)

        src = state.get("source", {})
        title = ""
        if settings.title_overlay:
            title = (script.get("overlay_title") or script.get("title") or "").strip()
        credit = ""
        if settings.credit_format and src.get("channel"):
            credit = settings.credit_format.format(channel=src["channel"])

        total = float(clip.get("duration") or 0) + float(
            state.get("timeline", {}).get("added") or 0
        )

        content = sub_tool.build_ass(
            dialogue=dialogue, narrations=narration_subs, gags=gag_subs,
            speaker_names=state.get("speaker_map", {}),
            width=out_w, height=out_h, size=size,
            video_top=video_top, video_bottom=video_bottom,
            title=title, credit=credit, total_duration=total,
        )
        workdir: Path = settings.output_path / state["job_id"]
        ass_path = sub_tool.write_ass(content, workdir / "subtitles.ass")
        srt_path = sub_tool.write_srt(dialogue, narration_subs, workdir / "subtitles.srt")

        payload = {
            "ass": str(ass_path),
            "srt": str(srt_path),
            "srt_url": f"/api/artifacts/{state['job_id']}/subtitles.srt",
            "ass_url": f"/api/artifacts/{state['job_id']}/subtitles.ass",
            "out_w": out_w, "out_h": out_h, "reframe": bool(reframe),
            "video_top": round(video_top), "video_bottom": round(video_bottom),
            "title": title, "credit": credit,
            "font": font_family(),
            "counts": {"dialogue": len(dialogue), "narration": len(narration_subs),
                       "gag": len(gag_subs)},
            "events": {
                "dialogue": dialogue[:200],
                "narration": narration_subs,
                "gag": gag_subs,
            },
        }
        emit("subtitle", subtitle=payload)
        log(f"자막 {len(dialogue)}대사 + {len(narration_subs)}나레이션 + {len(gag_subs)}드립 "
            f"→ {out_w}x{out_h}, 폰트 {payload['font']}", node=NAME)
        node_status(NAME, "done", **payload["counts"])
        return {"subtitle": payload}
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[subtitler] {exc}"]}
