"""ASS 자막 생성 — 화자 대사 / 나레이션 / 드립 3종 스타일.

libass 로 burn-in 하므로 폰트만 잡히면 한글이 그대로 나온다.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings
from .fonts import font_family


def _color(raw: str, default: str = "&H00FFFFFF") -> str:
    """'&H00A5FF&' / '00A5FF' / '&H0000A5FF' 를 ASS 의 &HAABBGGRR 로 정규화."""
    s = (raw or "").strip().replace("&H", "").replace("&h", "").rstrip("&")
    if len(s) == 6:
        s = "00" + s
    if len(s) != 8:
        return default
    try:
        int(s, 16)
    except ValueError:
        return default
    return "&H" + s.upper()


def ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def chunk_line(
    text: str, start: float, end: float,
    *, max_chars: int | None = None, min_dur: float | None = None,
) -> list[dict]:
    """긴 대사 한 줄을 2~3어절씩 끊어 여러 자막으로 나눈다.

    쇼츠 자막은 문장 통째로 띄우지 않고 짧게 끊어 빠르게 넘긴다.
    단어 단위 타임스탬프가 없어도 되도록, 각 조각의 글자 수에 비례해
    구간을 나눈다. 조각이 너무 잘게 쪼개져 순간적으로 스쳐 지나가지 않게
    min_dur 을 만족할 만큼만 나눈다.
    """
    max_chars = max_chars or settings.subtitle_max_chars
    min_dur = min_dur or settings.subtitle_min_duration

    text = (text or "").strip()
    span = max(0.0, end - start)
    if not text:
        return []

    tokens = text.split()
    if not tokens:
        return []

    # 1) 어절을 max_chars 이하로 묶는다 (한 어절이 더 길면 쪼개지 않는다)
    chunks: list[str] = []
    cur = ""
    for tok in tokens:
        trial = f"{cur} {tok}".strip()
        if cur and len(trial) > max_chars:
            chunks.append(cur)
            cur = tok
        else:
            cur = trial
    if cur:
        chunks.append(cur)

    # 2) 구간이 짧으면 조각 수를 줄여 최소 노출 시간을 확보한다
    allowed = max(1, int(span // min_dur)) if span > 0 else 1
    while len(chunks) > allowed:
        # 가장 짧은 두 이웃을 합친다
        i = min(range(len(chunks) - 1), key=lambda k: len(chunks[k]) + len(chunks[k + 1]))
        chunks[i:i + 2] = [f"{chunks[i]} {chunks[i + 1]}"]

    # 3) 글자 수 비례로 시간 배분
    total = sum(len(c) for c in chunks) or 1
    out: list[dict] = []
    cursor = start
    for i, c in enumerate(chunks):
        dur = span * len(c) / total
        last = i == len(chunks) - 1
        seg_end = end if last else min(end, cursor + dur)
        if seg_end - cursor < 0.05:
            seg_end = min(end, cursor + 0.05)
        out.append({"start": round(cursor, 3), "end": round(seg_end, 3), "text": c})
        cursor = seg_end
    return out


def esc(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", "\\N")
        .strip()
    )


# 화자 구분용 팔레트 (ASS 는 &HBBGGRR). 노랑 → 흰색 → 하늘 → 연두 순
SPEAKER_PALETTE = [
    "&H004DE4FF",   # 노랑  #FFE44D
    "&H00FFFFFF",   # 흰색
    "&H00F0D090",   # 하늘  #90D0F0
    "&H00A0F0A0",   # 연두  #A0F0A0
    "&H00B0B0FF",   # 분홍  #FFB0B0
]
LABEL_COLOR = "&H00FFA0C9"   # 보라 #C9A0FF — (가위) (바위) 같은 라벨
TITLE_COLOR = "&H00FFFFFF"
CREDIT_COLOR = "&H00C8C8C8"


def balance_title(text: str, lines: int = 2) -> str:
    """상단 고정 타이틀을 두 줄로 균형 있게 접는다."""
    words = (text or "").split()
    if len(words) <= 1 or lines <= 1:
        return esc(text)
    target = len(text) / lines
    best, best_cost = None, None
    for cut in range(1, len(words)):
        a = " ".join(words[:cut])
        b = " ".join(words[cut:])
        cost = abs(len(a) - target) + abs(len(b) - target)
        if best_cost is None or cost < best_cost:
            best, best_cost = (a, b), cost
    return esc(best[0]) + "\\N" + esc(best[1])


def build_ass(
    *,
    dialogue: list[dict],
    narrations: list[dict],
    gags: list[dict],
    speaker_names: dict[str, str],
    width: int | None = None,
    height: int | None = None,
    size: int | None = None,
    video_top: float | None = None,
    video_bottom: float | None = None,
    title: str = "",
    credit: str = "",
    total_duration: float = 0.0,
) -> str:
    """레퍼런스(레터박스 + 상단 고정 타이틀 + 화자별 색 자막) 레이아웃으로 ASS 생성.

    video_top / video_bottom 은 최종 프레임에서 '실제 영상이 차지하는 세로 범위'다.
    자막을 검은 띠가 아니라 영상 안쪽 가장자리에 붙이기 위해 필요하다.
    """
    w = width or settings.video_width
    h = height or settings.video_height
    base = size or settings.subtitle_size
    family = font_family()

    # 영상 영역을 모르면 화면 전체로 간주
    vt = float(video_top if video_top is not None else 0)
    vb = float(video_bottom if video_bottom is not None else h)
    top_band = max(0.0, vt)              # 위쪽 검은 띠 높이
    bottom_band = max(0.0, h - vb)       # 아래쪽 검은 띠 높이

    video_h = max(1.0, vb - vt)
    side = round(w * 0.05)
    stroke = lambda s: max(3, round(s / 11))   # noqa: E731 — 레퍼런스처럼 두꺼운 외곽선

    # 레퍼런스 실측(442x784 프레임)에서 뽑은 비율
    dia_size = base
    title_size = round(base * 1.03)
    label_size = round(base * 0.85)
    nar_size = round(base * 0.76)
    credit_size = round(base * 0.49)

    # ── 세로 위치 ────────────────────────────────────────────────
    # 대사: 글자 아랫변이 영상 하단에서 영상높이의 16.6% 위 (얼굴을 안 가리는 높이)
    dia_mv = round(max(h * 0.02, h - (vb - video_h * 0.166)))
    # 라벨(가위/바위): 영상 상단 안쪽 6%
    label_mv = round(max(h * 0.01, vt + video_h * 0.06))
    # 타이틀: 위 검은 띠에서 영상 바로 위에 붙인다 (두 줄 기준)
    title_mv = round(max(h * 0.015, vt - title_size * 2.6 - h * 0.012))
    # 크레딧: 화면 최하단에서 10.3%
    credit_mv = round(h * 0.103)
    # 나레이션: 아래 검은 띠 안, 크레딧 위
    nar_floor = credit_mv + round(credit_size * 1.7)
    nar_mv = max(nar_floor, round(bottom_band * 0.45)) if bottom_band > h * 0.10 else \
        round(dia_mv + dia_size * 1.9)

    # ── 화자별 색 배정 ────────────────────────────────────────────
    speakers = sorted({d.get("speaker", "") for d in dialogue if d.get("speaker") is not None})
    speaker_style = {sp: f"Dia{i}" for i, sp in enumerate(speakers)}

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]

    # 화자 대사 — 영상 하단 안쪽, 화자마다 다른 색
    for i, sp in enumerate(speakers):
        color = SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)]
        lines.append(
            f"Style: Dia{i},{family},{dia_size},{color},&H000000FF,&H00000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,{stroke(dia_size)},1,2,{side},{side},{dia_mv},1"
        )
    if not speakers:
        lines.append(
            f"Style: Dia0,{family},{dia_size},{SPEAKER_PALETTE[0]},&H000000FF,&H00000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,{stroke(dia_size)},1,2,{side},{side},{dia_mv},1"
        )

    lines += [
        # 라벨/드립 — 영상 상단 안쪽, 보라
        f"Style: Label,{family},{label_size},{LABEL_COLOR},&H000000FF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{stroke(label_size)},1,8,{side},{side},{label_mv},1",
        # 상단 고정 타이틀 — 검은 띠 위, 흰색
        f"Style: Title,{family},{title_size},{TITLE_COLOR},&H000000FF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{stroke(title_size)},0,8,{side},{side},{title_mv},1",
        # 나레이션 — 아래 검은 띠
        f"Style: Narration,{family},{nar_size},{_color(settings.narration_color, '&H0000A5FF')},"
        f"&H000000FF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,{stroke(nar_size)},0,2,{side},{side},{nar_mv},1",
        # 출처 표기 — 최하단
        f"Style: Credit,{family},{credit_size},{CREDIT_COLOR},&H000000FF,&H00000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,1,0,2,{side},{side},{credit_mv},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[tuple[float, str]] = []

    # 영상 내내 떠 있는 고정 요소
    span = max(total_duration, 0.0)
    if title and span > 0:
        events.append((-2.0,
            f"Dialogue: 0,{ts(0)},{ts(span)},Title,,0,0,0,,{balance_title(title)}"))
    if credit and span > 0:
        events.append((-1.0,
            f"Dialogue: 0,{ts(0)},{ts(span)},Credit,,0,0,0,,{esc(credit)}"))

    for d in dialogue:
        text = esc(d.get("text", ""))
        if not text:
            continue
        style = speaker_style.get(d.get("speaker", ""), "Dia0")
        events.append((
            d["start"],
            f"Dialogue: 3,{ts(d['start'])},{ts(d['end'])},{style},,0,0,0,,{text}",
        ))

    for n in narrations:
        text = esc(n.get("text", ""))
        if not text:
            continue
        events.append((
            n["start"],
            f"Dialogue: 2,{ts(n['start'])},{ts(n['end'])},Narration,,0,0,0,,"
            f"{{\\fad(150,150)}}{text}",
        ))

    for g in gags:
        text = esc(g.get("text", ""))
        if not text:
            continue
        events.append((
            g["start"],
            f"Dialogue: 4,{ts(g['start'])},{ts(g['end'])},Label,,0,0,0,,"
            f"{{\\fad(90,140)}}{text}",
        ))

    events.sort(key=lambda x: x[0])
    lines += [e for _, e in events]
    return "\n".join(lines) + "\n"


def write_ass(content: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def write_srt(dialogue: list[dict], narrations: list[dict], path: Path) -> Path:
    """검수/업로드용 통합 SRT (드립은 제외)."""
    rows = sorted(
        [{"start": d["start"], "end": d["end"], "text": d["text"]} for d in dialogue]
        + [{"start": n["start"], "end": n["end"], "text": f"({n['text']})"} for n in narrations],
        key=lambda x: x["start"],
    )

    def stamp(sec: float) -> str:
        ms = int(round(max(0.0, sec) * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    out: list[str] = []
    for i, r in enumerate(rows, start=1):
        out += [str(i), f"{stamp(r['start'])} --> {stamp(r['end'])}", r["text"].strip(), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path
