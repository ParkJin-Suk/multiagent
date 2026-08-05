"""yt-dlp 로 원본 영상·메타·히트맵·자막을 확보한다.

로컬 파일 경로를 주면 다운로드 없이 그대로 사용한다 (테스트/오프라인용).
"""
from __future__ import annotations

import asyncio
import json
import time
import re
from pathlib import Path
from typing import Any

import httpx

from ..config import settings
from ..events import log
from . import media


def is_local(url: str) -> bool:
    return not re.match(r"^https?://", url.strip(), re.I)


def _base_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "nocheckcertificate": False,
    }
    if settings.ytdlp_cookies_from_browser:
        opts["cookiesfrombrowser"] = (settings.ytdlp_cookies_from_browser,)
    if settings.ytdlp_cookie_file:
        opts["cookiefile"] = str(settings.resolve(settings.ytdlp_cookie_file))
    return opts


# ─────────────────────────────────────────────────────────────────────
#  메타 + 히트맵
# ─────────────────────────────────────────────────────────────────────
def _probe_sync(url: str) -> dict[str, Any]:
    import yt_dlp

    with yt_dlp.YoutubeDL({**_base_opts(), "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return info or {}


async def fetch_info(url: str) -> dict[str, Any]:
    """영상 메타데이터 + heatmap + 자막 트랙 목록."""
    if is_local(url):
        path = Path(url).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"로컬 파일이 없습니다: {path}")
        info = await media.probe(path)
        return {
            "id": path.stem,
            "title": path.stem,
            "channel": "(로컬 파일)",
            "duration": info.get("duration", 0),
            "webpage_url": str(path),
            "thumbnail": "",
            "heatmap": None,
            "local_path": str(path),
            "width": info.get("width", 0),
            "height": info.get("height", 0),
            "subtitle_tracks": {},
        }

    raw = await asyncio.to_thread(_probe_sync, url)
    heatmap = raw.get("heatmap")

    subs: dict[str, list[dict]] = {}
    for key in ("subtitles", "automatic_captions"):
        for lang, tracks in (raw.get(key) or {}).items():
            subs.setdefault(lang, [])
            for t in tracks:
                subs[lang].append({"ext": t.get("ext"), "url": t.get("url"), "auto": key != "subtitles"})

    return {
        "id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "channel": raw.get("uploader") or raw.get("channel", ""),
        "duration": float(raw.get("duration") or 0),
        "webpage_url": raw.get("webpage_url", url),
        "thumbnail": raw.get("thumbnail", ""),
        "view_count": raw.get("view_count", 0),
        "language": raw.get("language") or "",
        "chapters": raw.get("chapters") or [],
        "heatmap": heatmap,
        "subtitle_tracks": subs,
        "local_path": None,
    }


# ─────────────────────────────────────────────────────────────────────
#  다운로드
# ─────────────────────────────────────────────────────────────────────
def _format_selector(audio_only: bool) -> str:
    """h264(avc1)+m4a 를 최우선으로. AV1/VP9 은 디코딩이 느려 컷·렌더가 전부 느려진다."""
    if audio_only:
        return "bestaudio/best"
    h = settings.max_height
    return (
        f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]/"
        f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={h}]+ba/b[height<={h}]/b"
    )


def _download_sync(
    url: str, out_tmpl: str, start: float | None, end: float | None,
    *, audio_only: bool = False, fast_cut: bool = False,
) -> str:
    import yt_dlp
    from yt_dlp.utils import download_range_func

    opts: dict[str, Any] = {
        **_base_opts(),
        "format": _format_selector(audio_only),
        "outtmpl": out_tmpl,
        "overwrites": True,
        "concurrent_fragment_downloads": 8,
    }
    if not audio_only:
        opts["merge_output_format"] = "mp4"

    if start is not None and end is not None:
        opts["download_ranges"] = download_range_func(None, [(start, end)])
        # force_keyframes_at_cuts=True 면 yt-dlp 가 구간을 '재인코딩' 한다.
        # 정확한 컷을 얻는 대신 느리므로, 인코더 옵션을 직접 넣어 속도를 끌어올린다.
        # (기본값은 preset=medium 이라 몇 배 느리다)
        opts["force_keyframes_at_cuts"] = not fast_cut
        if not fast_cut:
            opts["external_downloader_args"] = {
                "ffmpeg_o": ["-preset", "veryfast", "-crf", "20", "-b:a", "192k"],
            }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)


async def download(
    url: str, workdir: Path, start: float | None = None, end: float | None = None,
    *, audio_only: bool = False,
) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)

    if is_local(url):
        src = Path(url).expanduser()
        if start is None or end is None:
            return src
        dst = workdir / "clip.mp4"
        return await media.cut(src, dst, start, end)

    stem = "audio" if audio_only else "source"
    tmpl = str(workdir / f"{stem}.%(ext)s")
    fast_cut = settings.ytdlp_fast_cut and start is not None

    label = f"{start:.1f}~{end:.1f}s 구간" if start is not None else "전체"
    if audio_only:
        label += " · 오디오만"
    log(f"yt-dlp 다운로드 시작 ({label}"
        + (", 빠른 컷 모드" if fast_cut else "") + ")")
    if fast_cut and settings.stt_provider == "subtitles":
        log("빠른 컷 모드는 구간 시작이 키프레임까지 최대 몇 초 앞당겨집니다. "
            "STT_PROVIDER=subtitles 라면 자막이 그만큼 밀릴 수 있어요.", level="warn")

    task = asyncio.create_task(asyncio.to_thread(
        _download_sync, url, tmpl, start, end, audio_only=audio_only, fast_cut=fast_cut,
    ))
    await _heartbeat(task, workdir, stem)
    path = await task

    p = Path(path)
    if not p.exists():
        p = next((c for c in sorted(workdir.glob(f"{stem}.*"))
                  if c.suffix in (".mp4", ".mkv", ".webm", ".m4a", ".opus", ".ogg")), p)
    if not p.exists():
        raise RuntimeError("다운로드된 파일을 찾지 못했습니다.")
    log(f"다운로드 완료: {p.name} ({p.stat().st_size / 1024 / 1024:.1f}MB)")
    return p


async def _heartbeat(task: asyncio.Task, workdir: Path, stem: str, every: float = 6.0) -> None:
    """yt-dlp 의 ffmpeg 다운로더는 진행률 콜백을 주지 않는다.
    화면이 멈춘 것처럼 보이지 않도록 경과 시간과 받은 용량을 주기적으로 알린다."""
    waited = 0.0
    while True:
        started = time.monotonic()
        done, _ = await asyncio.wait({task}, timeout=every)
        waited += time.monotonic() - started
        if done:
            return
        got = sum(f.stat().st_size for f in workdir.glob(f"{stem}*") if f.is_file())
        log(f"다운로드 중… {waited:.0f}초 경과 ({got / 1024 / 1024:.1f}MB)")


# ─────────────────────────────────────────────────────────────────────
#  자막 트랙 (STT 폴백용)
# ─────────────────────────────────────────────────────────────────────
LANG_PRIORITY = ["en", "en-orig", "ja", "es", "zh-Hans", "ko"]


async def fetch_subtitle_segments(
    subtitle_tracks: dict[str, list[dict]], prefer: str = ""
) -> tuple[list[dict], str]:
    """유튜브 자막을 [{start,end,text}] 로 변환. (세그먼트, 언어코드) 반환."""
    if not subtitle_tracks:
        return [], ""

    order = ([prefer] if prefer else []) + LANG_PRIORITY + list(subtitle_tracks.keys())
    for lang in order:
        tracks = subtitle_tracks.get(lang)
        if not tracks:
            continue
        for ext in ("json3", "vtt", "srv3"):
            track = next((t for t in tracks if t.get("ext") == ext and t.get("url")), None)
            if not track:
                continue
            try:
                async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
                    body = (await client.get(track["url"])).text
            except Exception as exc:  # noqa: BLE001
                log(f"자막 다운로드 실패({lang}/{ext}): {exc}", level="warn")
                continue
            segs = _parse_json3(body) if ext in ("json3", "srv3") else _parse_vtt(body)
            if segs:
                log(f"유튜브 자막 사용: {lang} ({ext}, {len(segs)}줄)")
                return segs, lang
    return [], ""


def _parse_json3(body: str) -> list[dict]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for ev in data.get("events", []):
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        start = (ev.get("tStartMs", 0)) / 1000
        dur = (ev.get("dDurationMs", 0)) / 1000
        out.append({"start": start, "end": start + max(dur, 0.6), "text": text})
    return out


TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")


def _parse_vtt(body: str) -> list[dict]:
    out: list[dict] = []
    block: list[str] = []
    start = end = None
    for line in body.splitlines() + [""]:
        if "-->" in line:
            stamps = TS.findall(line)
            if len(stamps) >= 2:
                start, end = _to_sec(stamps[0]), _to_sec(stamps[1])
            block = []
        elif line.strip() == "":
            if start is not None and block:
                text = re.sub(r"<[^>]+>", "", " ".join(block)).strip()
                if text:
                    out.append({"start": start, "end": end, "text": text})
            start = end = None
            block = []
        elif start is not None and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            block.append(line.strip())
    # 자동자막은 같은 문장이 겹쳐 나오므로 중복 제거
    dedup: list[dict] = []
    for seg in out:
        if dedup and seg["text"] == dedup[-1]["text"]:
            dedup[-1]["end"] = seg["end"]
            continue
        dedup.append(seg)
    return dedup


def _to_sec(parts: tuple[str, str, str, str]) -> float:
    h, m, s, ms = parts
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
