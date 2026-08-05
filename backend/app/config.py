"""전역 설정 로더."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ROOT_DIR / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────
    llm_model: str = "openai:gpt-4o-mini"
    llm_writer_model: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # ── yt-dlp ───────────────────────────────────────────────────────
    ytdlp_cookies_from_browser: str = ""
    ytdlp_cookie_file: str = ""
    max_height: int = 1080
    # 구간만 잘라 받기. 유튜브에서는 오히려 훨씬 느리다 (아래 주석 참고)
    download_sections_only: bool = False
    # 전체 다운로드가 부담되는 긴 영상은 이 길이를 넘으면 구간 다운로드로 전환
    section_download_over_minutes: float = 45
    ytdlp_fast_cut: bool = False

    # ── 하이라이트 ───────────────────────────────────────────────────
    highlight_strategy: str = "auto"      # heatmap | audio | auto
    clip_min_seconds: float = 35
    clip_max_seconds: float = 90
    candidate_count: int = 4
    review_clip_selection: bool = True

    # ── STT ──────────────────────────────────────────────────────────
    stt_provider: str = "subtitles"       # whisperx | faster-whisper | subtitles
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "float16"
    source_language: str = "auto"
    min_speakers: int = 0
    max_speakers: int = 0
    hf_token: str = ""

    # ── 번역 ─────────────────────────────────────────────────────────
    target_language: str = "ko"
    translation_style: str = "자연스러운 한국어 구어체."

    # ── TTS ──────────────────────────────────────────────────────────
    tts_provider: str = "edge"            # edge | typecast | none
    edge_voice: str = "ko-KR-InJoonNeural"
    edge_rate: int = 0                    # 기본 말속도 보정 (%)
    edge_pitch: int = 0                   # 기본 음높이 보정 (Hz)
    edge_volume: int = 0                  # 기본 볼륨 보정 (%)
    # 아래는 TTS_PROVIDER=typecast 일 때만 사용
    typecast_api_key: str = ""
    typecast_voice_id: str = ""
    typecast_model: str = "ssfm-v30"
    typecast_emotion: str = "normal"
    typecast_emotion_intensity: float = 1.0

    # ── 나레이션 삽입 ────────────────────────────────────────────────
    narration_mode: str = "auto"          # duck | freeze | auto
    duck_level: float = 0.25
    max_narrations: int = 6
    max_gags: int = 8

    # ── 자막/영상 ────────────────────────────────────────────────────
    video_width: int = 1080
    video_height: int = 1920
    vertical_reframe: bool = True
    # black = 레퍼런스처럼 위아래 검은 띠 / blur = 원본을 확대·블러해 배경으로
    letterbox_style: str = "black"
    # 상단에 영상 내내 떠 있는 고정 타이틀
    title_overlay: bool = True
    # 하단 출처 표기. {channel} 자리에 원본 채널명이 들어간다. 비우면 표시 안 함
    credit_format: str = "© {channel}"
    font_path: str = ""
    font_name: str = ""
    subtitle_size: int = 106
    # 자막 한 조각의 최대 글자 수 / 최소 노출 시간
    subtitle_max_chars: int = 13
    subtitle_min_duration: float = 0.45
    narration_color: str = "&H00A5FF&"
    dialogue_color: str = "&HFFFFFF&"
    gag_color: str = "&H00F5FF&"

    output_dir: str = "./output"

    # ── 파생 ─────────────────────────────────────────────────────────
    @property
    def output_path(self) -> Path:
        p = Path(self.output_dir)
        if not p.is_absolute():
            p = ROOT_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def writer_model(self) -> str:
        return self.llm_writer_model or self.llm_model

    def resolve(self, raw: str) -> Path:
        p = Path(raw)
        return p if p.is_absolute() else ROOT_DIR / p


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", s.openai_api_key)
    if s.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", s.anthropic_api_key)
    if s.hf_token:
        os.environ.setdefault("HF_TOKEN", s.hf_token)
        os.environ.setdefault("HUGGINGFACE_HUB_TOKEN", s.hf_token)
    return s


settings = get_settings()
