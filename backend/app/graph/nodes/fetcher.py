"""① 영상 확보 에이전트 — yt-dlp 로 메타·히트맵·자막을 가져온다.

이 단계에서는 아직 영상을 통째로 받지 않는다. 하이라이트 구간이 정해진 뒤에
그 구간만 받는 게 훨씬 빠르기 때문. 다만 heatmap 이 없어서 오디오 분석이
필요한 경우에는 어쩔 수 없이 오디오만 먼저 받는다.
"""
from __future__ import annotations

from pathlib import Path

from ...config import settings
from ...events import emit, log, node_status
from ...tools import media, source
from ..state import ClipState

NAME = "fetcher"


async def fetcher(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    url = (cfg.get("url") or "").strip()
    if not url:
        node_status(NAME, "error", message="URL 이 비어 있습니다.")
        return {"errors": ["[fetcher] URL 없음"]}

    try:
        media.ensure_ffmpeg()
        info = await source.fetch_info(url)
        heatmap = info.get("heatmap") or []
        duration = float(info.get("duration") or 0)

        log(f"소스 확보: {info.get('title')} ({duration:.0f}초)", node=NAME)
        if heatmap:
            log(f"heatmap {len(heatmap)}구간 확보 — '가장 많이 다시 본 장면' 사용 가능", node=NAME)
        else:
            log("heatmap 이 없는 영상입니다 (조회수가 적거나 비공개 지표) "
                "→ 오디오 에너지 분석으로 전환합니다.", level="warn", node=NAME)

        # heatmap 이 없으면 오디오만 먼저 받아서 분석해야 한다
        audio_path: Path | None = None
        workdir = settings.output_path / state["job_id"]
        strategy = (cfg.get("highlight_strategy") or settings.highlight_strategy).lower()
        need_audio = (not heatmap) and strategy in ("auto", "audio", "heatmap")

        if need_audio:
            log("분석용 오디오 다운로드 중… (영상은 받지 않습니다)", node=NAME)
            src_media = await source.download(url, workdir, audio_only=True)
            audio_path = await media.extract_audio(src_media, workdir / "full_audio.wav")
            if not duration:
                duration = (await media.probe(src_media)).get("duration", 0)

        # 자막 트랙 (STT_PROVIDER=subtitles 폴백용)
        subs, sub_lang = [], ""
        if info.get("subtitle_tracks"):
            subs, sub_lang = await source.fetch_subtitle_segments(
                info["subtitle_tracks"],
                prefer=cfg.get("source_language") or settings.source_language,
            )

        payload = {
            "id": info.get("id", ""),
            "title": info.get("title", ""),
            "channel": info.get("channel", ""),
            "duration": round(duration, 2),
            "url": info.get("webpage_url", url),
            "thumbnail": info.get("thumbnail", ""),
            "view_count": info.get("view_count", 0),
            "local_path": info.get("local_path"),
            "audio_path": str(audio_path) if audio_path else None,
            "has_heatmap": bool(heatmap),
            "chapters": info.get("chapters", [])[:20],
        }
        emit("source", source=payload, subtitle_lines=len(subs), subtitle_language=sub_lang)
        node_status(NAME, "done", title=payload["title"], duration=payload["duration"],
                    has_heatmap=payload["has_heatmap"])
        return {
            "source": payload,
            "heatmap": heatmap,
            "subtitle_segments": subs,
            "subtitle_language": sub_lang,
            "logs": [f"소스 확보: {payload['title']}"],
        }
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[fetcher] {exc}"]}
