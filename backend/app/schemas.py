"""LLM 구조화 출력 & API 스키마."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────
#  1. 소스 / 하이라이트
# ─────────────────────────────────────────────────────────────────────


class HeatPoint(BaseModel):
    start: float
    end: float
    value: float


class Candidate(BaseModel):
    index: int
    start: float
    end: float
    score: float = 0.0
    source: str = "heatmap"      # heatmap | audio | manual
    reason: str = ""


class CandidateRanking(BaseModel):
    """LLM 이 후보 구간을 재정렬한 결과."""

    chosen_index: int = Field(description="가장 클립으로 만들기 좋은 후보의 index")
    reason: str = Field(description="그 구간을 고른 이유 한두 문장")
    hook_guess: str = Field(default="", description="이 구간에서 터질 만한 포인트 추측")


# ─────────────────────────────────────────────────────────────────────
#  2. STT / 화자
# ─────────────────────────────────────────────────────────────────────


class Utterance(BaseModel):
    index: int = 0
    start: float
    end: float
    speaker: str = "SPEAKER_00"
    text: str = ""
    kind: Literal["speech", "sound"] = "speech"


class SpeakerName(BaseModel):
    speaker_id: str = Field(description="SPEAKER_00 같은 원본 라벨")
    name: str = Field(description="자막에 쓸 짧은 한국어 호칭. 예: 진행자, 남자1, 심사위원")
    note: str = ""


class SpeakerMap(BaseModel):
    speakers: list[SpeakerName]
    summary: str = Field(description="이 클립에서 무슨 일이 벌어지는지 3문장 요약")


# ─────────────────────────────────────────────────────────────────────
#  3. 번역
# ─────────────────────────────────────────────────────────────────────


class TranslatedLine(BaseModel):
    index: int = Field(description="원본 발화 index 그대로")
    ko: str = Field(description="번역된 한국어 자막 한 줄")


class TranslationResult(BaseModel):
    lines: list[TranslatedLine]


# ─────────────────────────────────────────────────────────────────────
#  4. 스크립트 (나레이션 + 드립)
# ─────────────────────────────────────────────────────────────────────


class Narration(BaseModel):
    start: float = Field(description="나레이션이 시작될 클립 내 초 단위 시각")
    text: str = Field(description="TTS 로 읽을 나레이션. 25자 내외 한 문장")
    reason: str = Field(default="", description="왜 여기에 넣는지")
    emotion: str = Field(default="normal", description="normal|happy|sad|angry|whisper")


class Gag(BaseModel):
    start: float = Field(description="드립 자막이 뜨는 시각")
    duration: float = Field(default=1.8, description="노출 시간(초)")
    text: str = Field(description="화면에만 뜨는 드립. 15자 내외, 음성 없음")


class ClipScript(BaseModel):
    context: str = Field(description="클립 전체 맥락 2~3문장")
    overlay_title: str = Field(
        default="",
        description="영상 상단에 처음부터 끝까지 박아둘 한 줄 컨셉. "
                    "14자 내외. 예: '가위바위보가 사람이 된다면'",
    )
    narrations: list[Narration] = Field(default_factory=list)
    gags: list[Gag] = Field(default_factory=list)
    title: str = Field(default="", description="유튜브 제목 40자 이내")
    description: str = Field(default="", description="설명글 2~3문장")
    tags: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
#  5. API
# ─────────────────────────────────────────────────────────────────────


class RunRequest(BaseModel):
    url: str = Field(description="유튜브 URL 또는 로컬 파일 경로")
    translation_style: str = ""
    narration_persona: str = ""     # 나레이터 캐릭터 설정
    gag_level: int = 2              # 0=드립 없음 ~ 3=드립 많이
    clip_seconds: float = 0         # 0 이면 .env 범위 자동
    narration_mode: str = ""        # duck | freeze | auto
    vertical_reframe: bool | None = None
    review_clip_selection: bool | None = None
    manual_start: float | None = None   # 구간을 직접 지정할 때
    manual_end: float | None = None


class ClipDecision(BaseModel):
    """구간 선택 게이트 응답."""

    candidate_index: int | None = None
    start: float | None = None
    end: float | None = None
    cancel: bool = False
