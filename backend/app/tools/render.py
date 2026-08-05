"""최종 합성 — 나레이션 삽입(덕킹/프레임 정지) + 세로 리프레임 + 자막 burn-in."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import settings
from ..events import log
from . import media
from .fonts import find_korean_font


# ─────────────────────────────────────────────────────────────────────
#  나레이션 배치 계획
# ─────────────────────────────────────────────────────────────────────
@dataclass
class NarrationSlot:
    index: int
    at: float               # 원본 클립 기준 시각
    duration: float
    audio: Path
    text: str
    mode: str = "duck"      # duck | freeze
    final_at: float = 0.0   # 최종 타임라인 기준 시각
    gap_len: float = 0.0


@dataclass
class Timeline:
    """원본 클립 시간 → 최종 영상 시간 매핑."""

    inserts: list[tuple[float, float]] = field(default_factory=list)  # (at, 늘어난 길이)

    def map(self, t: float) -> float:
        return t + sum(d for at, d in self.inserts if at <= t + 1e-6)

    @property
    def added(self) -> float:
        return sum(d for _, d in self.inserts)


def plan(
    slots: list[NarrationSlot],
    gaps: list[dict],
    *,
    mode: str | None = None,
    tolerance: float = 0.4,
) -> Timeline:
    """각 나레이션을 덕킹으로 넣을지 프레임 정지로 넣을지 정하고 최종 시각을 계산."""
    mode = (mode or settings.narration_mode).lower()
    timeline = Timeline()

    for slot in sorted(slots, key=lambda s: s.at):
        gap = next(
            (g for g in gaps if g["start"] - tolerance <= slot.at <= g["end"] + tolerance), None
        )
        slot.gap_len = round(gap["end"] - slot.at, 2) if gap else 0.0

        if mode == "duck":
            slot.mode = "duck"
        elif mode == "freeze":
            slot.mode = "freeze"
        else:  # auto — 갭이 나레이션 길이를 감당하면 덕킹, 아니면 정지 삽입
            slot.mode = "duck" if slot.gap_len >= slot.duration - tolerance else "freeze"

        slot.final_at = round(timeline.map(slot.at), 3)
        if slot.mode == "freeze":
            timeline.inserts.append((slot.at, slot.duration))

    return timeline


# ─────────────────────────────────────────────────────────────────────
#  ffmpeg 경로 이스케이프 (필터 인자용)
# ─────────────────────────────────────────────────────────────────────
def _esc(path: Path | str) -> str:
    s = str(path).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace("[", r"\[").replace("]", r"\]")


# ─────────────────────────────────────────────────────────────────────
#  1단계 — 프레임 정지 삽입
# ─────────────────────────────────────────────────────────────────────
async def insert_freezes(
    clip: Path, slots: list[NarrationSlot], workdir: Path, spec: dict
) -> Path:
    freezes = sorted([s for s in slots if s.mode == "freeze"], key=lambda s: s.at)
    if not freezes:
        return clip

    parts_dir = workdir / "freeze_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    w, h, fps = spec["width"], spec["height"], spec["fps"]
    total = spec["duration"]

    enc = [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-s", f"{w}x{h}",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
    ]

    parts: list[Path] = []
    cursor = 0.0
    for i, slot in enumerate(freezes):
        at = max(0.0, min(slot.at, total))
        if at - cursor > 0.08:
            seg = parts_dir / f"v{i:02d}.mp4"
            await media.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{cursor:.3f}", "-to", f"{at:.3f}", "-i", str(clip),
                *enc, "-movflags", "+faststart", str(seg),
            ], label="구간 분할")
            parts.append(seg)

        still = parts_dir / f"f{i:02d}.png"
        await media.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at - 0.05):.3f}", "-i", str(clip),
            "-frames:v", "1", str(still),
        ], label="정지 프레임 추출")

        hold = parts_dir / f"h{i:02d}.mp4"
        await media.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-loop", "1", "-i", str(still), "-i", str(slot.audio),
            "-t", f"{slot.duration:.3f}",
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
            *enc, "-shortest", "-movflags", "+faststart", str(hold),
        ], label="정지 구간 생성")
        parts.append(hold)
        cursor = at

    if total - cursor > 0.08:
        seg = parts_dir / "v_tail.mp4"
        await media.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{cursor:.3f}", "-i", str(clip),
            *enc, "-movflags", "+faststart", str(seg),
        ], label="구간 분할")
        parts.append(seg)

    listfile = parts_dir / "concat.txt"
    listfile.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in parts), encoding="utf-8"
    )
    out = workdir / "with_freeze.mp4"
    try:
        await media.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listfile),
            "-c", "copy", "-movflags", "+faststart", str(out),
        ], label="정지 구간 이어붙이기")
    except RuntimeError:
        log("스트림 복사 concat 실패 → 재인코딩", level="warn")
        await media.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listfile),
            *enc, "-movflags", "+faststart", str(out),
        ], label="정지 구간 이어붙이기")
    log(f"프레임 정지 {len(freezes)}곳 삽입 (+{sum(s.duration for s in freezes):.1f}초)")
    return out


# ─────────────────────────────────────────────────────────────────────
#  2단계 — 덕킹 믹스 + 리프레임 + 자막
# ─────────────────────────────────────────────────────────────────────
async def finalize(
    base: Path,
    slots: list[NarrationSlot],
    ass_path: Path | None,
    out_path: Path,
    *,
    reframe: bool,
    out_w: int,
    out_h: int,
    duck_level: float | None = None,
) -> Path:
    duck_level = settings.duck_level if duck_level is None else duck_level
    ducks = sorted([s for s in slots if s.mode == "duck"], key=lambda s: s.final_at)

    inputs: list[str] = ["-i", str(base)]
    for s in ducks:
        inputs += ["-i", str(s.audio)]

    chains: list[str] = []

    # ── 비디오 ──────────────────────────────────────────────────────
    if reframe and (settings.letterbox_style or "black").lower() == "blur":
        chains.append(
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},gblur=sigma=26,eq=brightness=-0.10:saturation=0.85[bg];"
            f"[fgsrc]scale={out_w}:-2:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vbase]"
        )
    elif reframe:
        # 레퍼런스와 동일한 검은 레터박스: 폭을 꽉 채우고 위아래를 검게
        chains.append(
            f"[0:v]scale={out_w}:-2:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[vbase]"
        )
    else:
        chains.append(f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                      f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2[vbase]")

    if ass_path and Path(ass_path).exists():
        fontdir = find_korean_font()
        fd = f":fontsdir='{_esc(Path(fontdir).parent)}'" if fontdir else ""
        chains.append(f"[vbase]ass='{_esc(ass_path)}'{fd}[vout]")
    else:
        chains.append("[vbase]null[vout]")

    # ── 오디오 ──────────────────────────────────────────────────────
    if ducks:
        ranges = "+".join(
            f"between(t,{max(0.0, s.final_at - 0.15):.3f},{s.final_at + s.duration + 0.25:.3f})"
            for s in ducks
        )
        chains.append(
            f"[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"volume={duck_level}:enable='{ranges}'[orig]"
        )
        labels = []
        for i, s in enumerate(ducks):
            lbl = f"n{i}"
            chains.append(
                f"[{i + 1}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                f"adelay={int(s.final_at * 1000)}|{int(s.final_at * 1000)},volume=1.35[{lbl}]"
            )
            labels.append(f"[{lbl}]")
        chains.append(
            f"[orig]{''.join(labels)}amix=inputs={len(ducks) + 1}:normalize=0:"
            f"duration=first:dropout_transition=0[aout]"
        )
        amap = ["-map", "[aout]"]
    else:
        amap = ["-map", "0:a?"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    await media.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[vout]", *amap,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(out_path),
    ], label="최종 렌더")
    return out_path


async def thumbnail(video: Path, at: float, out_path: Path) -> Path:
    await media.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, at):.3f}", "-i", str(video),
        "-frames:v", "1", "-q:v", "3", str(out_path),
    ], label="썸네일 추출")
    return out_path
