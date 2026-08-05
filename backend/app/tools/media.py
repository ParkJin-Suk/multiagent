"""ffmpeg / ffprobe 공통 유틸.

외부 프로세스는 `asyncio.create_subprocess_exec` 가 아니라
`asyncio.to_thread(subprocess.run, ...)` 으로 돌린다.

이유: 윈도우에서 asyncio 서브프로세스는 ProactorEventLoop 에서만 동작한다.
uvicorn 0.36+ 는 `--reload` 나 `--workers>1` 이면 SelectorEventLoop 를 고르는데
(uvicorn/loops/asyncio.py), 그 루프에서는 create_subprocess_exec 이
NotImplementedError 를 던진다 — 메시지가 빈 문자열이라 원인 추적도 어렵다.
표준 subprocess 를 스레드에서 돌리면 이벤트 루프 종류와 무관해진다.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

# 윈도우에서 ffmpeg 를 부를 때마다 콘솔 창이 깜빡이는 것을 막는다.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def ensure_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise RuntimeError(
                f"{binary} 를 찾을 수 없습니다. ffmpeg 를 설치해 주세요.\n"
                "  macOS: brew install ffmpeg / Ubuntu: sudo apt install ffmpeg"
            )


def _spawn(args: list[str], *, want_stdout: bool) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE if want_stdout else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL if want_stdout else subprocess.PIPE,
        **_NO_WINDOW,
    )


async def run(args: list[str], *, label: str = "ffmpeg") -> None:
    try:
        proc = await asyncio.to_thread(_spawn, args, want_stdout=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{label} 실패: 실행 파일을 찾을 수 없습니다 ({args[0]})") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"{label} 실패:\n" + (proc.stderr or b"").decode(errors="ignore")[-1800:]
        )


async def capture(args: list[str]) -> str:
    try:
        proc = await asyncio.to_thread(_spawn, args, want_stdout=True)
    except FileNotFoundError:
        return ""
    return (proc.stdout or b"").decode(errors="ignore")


async def capture_bytes(args: list[str]) -> bytes:
    try:
        proc = await asyncio.to_thread(_spawn, args, want_stdout=True)
    except FileNotFoundError:
        return b""
    return proc.stdout or b""


async def probe(path: str | Path) -> dict:
    raw = await capture([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    fmt = data.get("format", {})
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(fmt.get("duration", 0) or 0),
        "size": int(fmt.get("size", 0) or 0),
        "width": int(v.get("width", 0) or 0),
        "height": int(v.get("height", 0) or 0),
        "fps": _fps(v.get("r_frame_rate", "")),
        "has_video": bool(v),
        "has_audio": bool(a),
    }


def _fps(raw: str) -> float:
    try:
        num, den = raw.split("/")
        return round(float(num) / float(den), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


async def extract_audio(src: Path, dst: Path, *, rate: int = 16000, mono: bool = True) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    await run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-vn", "-ac", "1" if mono else "2", "-ar", str(rate),
        "-c:a", "pcm_s16le", str(dst),
    ], label="오디오 추출")
    return dst


async def cut(src: Path, dst: Path, start: float, end: float) -> Path:
    """정확한 컷을 위해 재인코딩한다 (키프레임 문제 회피)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    await run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ], label="구간 자르기")
    return dst


async def audio_rms_profile(path: Path, window: float = 1.0, rate: int = 4000) -> list[float]:
    """window 초 단위 RMS(0~1) 프로파일. 하이라이트 폴백용.

    저비트레이트 PCM 으로 디코드해서 파이썬에서 직접 계산한다
    (astats 메타데이터 파싱보다 ffmpeg 버전 의존성이 적다).
    """
    import array
    import math

    raw = await capture_bytes([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-",
    ])
    if not raw:
        return []

    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])

    step = max(1, int(rate * window))
    out: list[float] = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step]
        if not chunk:
            continue
        acc = 0
        for s in chunk:
            acc += s * s
        out.append(math.sqrt(acc / len(chunk)) / 32768.0)
    return out