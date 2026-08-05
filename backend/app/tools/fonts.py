"""한글 폰트 탐색 유틸."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..config import ROOT_DIR, settings
from ..events import log

CANDIDATES = [
    # 프로젝트 동봉
    ROOT_DIR / "assets" / "fonts",
    # macOS
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    # Linux
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
    # Windows
    Path("C:/Windows/Fonts"),
]

PREFERRED = [
    "Pretendard-Bold",
    "NotoSansKR-Bold",
    "NotoSansCJK-Bold",
    "NotoSansCJKkr-Bold",
    "NanumGothicBold",
    "NanumGothic",
    "malgunbd",
    "malgun",
    "AppleSDGothicNeo",
    "AppleGothic",
    "SourceHanSansK",
    "DejaVuSans-Bold",  # 최후 폴백 (한글 미지원)
    "DejaVuSans",
]

EXTS = {".ttf", ".otf", ".ttc"}


@lru_cache
def find_korean_font() -> str | None:
    if settings.font_path:
        p = settings.resolve(settings.font_path)
        if p.exists():
            return str(p)
        log(f"FONT_PATH 가 존재하지 않습니다: {p}", level="warn")

    found: dict[str, str] = {}
    for root in CANDIDATES:
        if not root.exists():
            continue
        try:
            for f in root.rglob("*"):
                if f.suffix.lower() in EXTS:
                    found.setdefault(f.stem.lower(), str(f))
        except (PermissionError, OSError):
            continue

    for name in PREFERRED:
        key = name.lower()
        for stem, path in found.items():
            if stem == key or stem.startswith(key):
                return path
    # 아무거나
    return next(iter(found.values()), None)


def font_warning() -> str | None:
    path = find_korean_font()
    if path is None:
        return "사용 가능한 폰트를 찾지 못했습니다. assets/fonts 에 ttf 를 넣어주세요."
    if "dejavu" in Path(path).stem.lower():
        return (
            "한글 폰트를 찾지 못해 DejaVuSans 로 대체합니다. 자막이 깨질 수 있으니 "
            "assets/fonts 에 NotoSansKR / Pretendard 등을 넣어주세요."
        )
    return None


def font_family(path: str | None = None) -> str:
    """ASS Fontname 으로 쓸 폰트 패밀리명. libass 가 이 이름으로 매칭한다."""
    from ..config import settings as _s

    if _s.font_name:
        return _s.font_name
    p = path or find_korean_font()
    if not p:
        return "Sans"
    try:
        from PIL import ImageFont

        family, _style = ImageFont.truetype(p, 20).getname()
        return family or "Sans"
    except Exception:  # noqa: BLE001
        return "Sans"


def font_dir(path: str | None = None) -> str:
    p = path or find_korean_font()
    return str(Path(p).parent) if p else "."
