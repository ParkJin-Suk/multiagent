# Reaction Factory — 번역·나레이션 클립 자동 생산 라인

긴 해외 영상 하나를 넣으면 **가장 많이 다시 본 구간**을 찾아 잘라내고,
화자별로 받아쓰기 → 말투 지정 번역 → 나레이션·드립 삽입 → TTS → 3종 자막 →
세로 쇼츠 mp4 까지 만들어 주는 LangGraph 멀티에이전트 파이프라인 + 웹 GUI.

```
fetcher ─▶ highlighter ─▶ clip_gate ─▶ transcriber ─▶ translator
영상확보    하이라이트      구간확정      STT·화자분리     말투번역
                             │
                             ▼
        scripter ─▶ voicer ─▶ subtitler ─▶ renderer ─▶ 완성
     나레이션·드립   TTS·배치     3종 자막    믹싱·합성
```

---

## 1. 각 에이전트가 하는 일

### ① fetcher — 영상 확보
`yt-dlp` 로 메타데이터·**heatmap**·자막 트랙을 가져온다. 이 단계에서 영상 본체는
받지 않는다(구간이 정해진 뒤 그 부분만 받는 게 훨씬 빠르다). heatmap 이 없는
영상이면 그때만 오디오를 먼저 받아 분석에 쓴다.

### ② highlighter — 하이라이트 추출
**중요:** 처음 구상하신 YouTube Analytics API 의 `elapsedVideoTimeRatio` /
`audienceWatchRatio` 는 **내가 소유한 채널의 영상만** 조회됩니다(OAuth 채널 권한).
남의 영상에는 쓸 수 없어요. 그래서 이 프로젝트는 세 단계로 갑니다.

| 순위 | 신호 | 설명 |
|---|---|---|
| 1 | **yt-dlp `heatmap`** | 유튜브 '가장 많이 다시 본 장면' 그래프를 그대로 파싱. `{start_time, end_time, value}` 약 100버킷. 조회수가 어느 정도 나온 영상이면 대부분 존재 |
| 2 | **오디오 RMS 에너지** | heatmap 이 없을 때. 웃음·환호·큰 소리 구간이 솟는다 |
| 3 | 도입부 폴백 | 둘 다 없을 때 |

두 경우 모두 '길이 L 슬라이딩 윈도우 점수 합' 이 최대인 구간을 겹치지 않게
N개 뽑고, 자막이 있으면 각 후보의 대사를 LLM 이 읽고 최종 1개를 고른다.

> 내 채널 영상에 Analytics API 를 쓰고 싶다면 `tools/highlight.py` 에
> `from_analytics()` 를 추가하고 `find_candidates()` 의 1순위로 끼우면 됩니다.
> 구조는 heatmap 과 동일한 `(t, score)` 곡선이라 그대로 붙습니다.

### ③ clip_gate — 구간 확정 (사람 개입)
LangGraph `interrupt()` 로 멈추고, 웹 화면의 **히트맵 그래프**에서 후보를
클릭하거나 드래그로 직접 구간을 지정한다. 확정하면 그 구간만 `yt-dlp` 의
`download_ranges` 로 받아 온다.
`REVIEW_CLIP_SELECTION=false` 로 두면 이 게이트 없이 자동으로 지나간다.

### ④ transcriber — STT + 화자분리
WhisperX(전사 → wav2vec 강제정렬 → pyannote 화자분리) 로 화자별 대사를 뽑고,
LLM 이 `SPEAKER_00` 같은 익명 라벨에 '진행자', '친구' 같은 한국어 호칭을 붙인다.
동시에 **대사가 없는 빈 구간(gap)** 목록을 계산한다 — 나레이션이 들어갈 자리다.

| STT_PROVIDER | 화자분리 | 설치 | 용도 |
|---|---|---|---|
| `whisperx` | ✅ | torch + whisperx + pyannote | 실사용 |
| `faster-whisper` | ❌ | faster-whisper | GPU 없을 때 |
| `subtitles` | ❌ | 없음 | 유튜브 자막 재활용, 테스트/오프라인 |

### ⑤ translator — 번역
40줄씩 배치로 병렬 번역. 화자별 말투 일관성을 유지하고, 자막 한 줄이
20자 안팎이 되도록 압축한다. 말투는 실행마다 웹에서 지정한다
(예: "반말 위주, 인터넷 방송 자막 톤, 욕설은 순화").

### ⑥ scripter — 나레이션·드립 작성
전체 대사 타임라인 + **실제 빈 구간 목록**을 LLM 에 주고 두 가지를 만들게 한다.

- **나레이션** — TTS 음성이 들어간다. 빈 구간 안에서만 시작 시각을 잡게 강제하고,
  결과가 구간 밖으로 나가면 코드가 가장 가까운 빈 구간으로 스냅한다.
- **드립** — 화면에만 뜨는 자막. 음성 없음. 대사 위에 겹쳐도 된다.
  `gag_level` 0~3 으로 개수를 조절하고, 0이면 하나도 만들지 않는다.

### ⑦ voicer — 나레이션 음성
**edge-tts** 로 나레이션만 합성한다. 무료이고 API 키가 필요 없다
(`pip install edge-tts`, requirements 에 포함).
**드립은 음성을 만들지 않고, 화자 대사는 원본 오디오를 그대로 쓴다.**

edge-tts 에는 감정 프리셋이 없어서, scripter 가 붙인 감정 태그를
말속도·음높이·볼륨 조합으로 옮긴다.

| 감정 | rate | pitch | volume |
|---|---|---|---|
| happy | +8% | +15Hz | 0 |
| excited | +12% | +20Hz | +5% |
| sad | -8% | -12Hz | -5% |
| angry | +10% | -5Hz | +10% |
| whisper | -5% | -5Hz | -35% |

여기에 `EDGE_RATE/PITCH/VOLUME` 기본 보정과 '빈 구간에 맞추려는 배속' 이 합산된다.
전송 실패는 2회까지 재시도하고, 그래도 안 되면 그 나레이션만 무음으로 넘어간다
(파이프라인은 계속 진행). 다만 **edge-tts 가 설치되어 있지 않으면** 조용히 넘기지
않고 설치 방법을 담은 에러를 띄운다 — 무음 나레이션으로 끝나면 원인을 찾기 어려워서다.

`TTS_PROVIDER=typecast` 로 두고 키를 넣으면 Typecast API 를 쓰고, 실패 시 edge-tts 로
폴백한다. `TTS_PROVIDER=none` 이면 합성하지 않고 나레이션 자리를 무음으로 둔다.

만든 음성이 빈 구간보다 조금 길면 1.25배속까지 눌러 담고, 그래도 안 되면
프레임 정지 삽입으로 전환한다.

| NARRATION_MODE | 동작 |
|---|---|
| `duck` | 나레이션 구간에서 원본 볼륨을 `DUCK_LEVEL` 로 낮춤. 영상 길이 유지 |
| `freeze` | 나레이션마다 화면을 정지시키고 끼워 넣음. 영상이 길어짐 |
| `auto` | 갭이 감당되면 duck, 모자라면 freeze (기본값) |

### ⑧ subtitler — 자막
`libass` 용 **ASS** 파일을 만든다. 레이아웃은 한국 리액션 쇼츠에서 가장 흔한
'검은 레터박스 + 상단 고정 타이틀' 형태를 따른다.

```
┌──────────────────┐
│   무모한 실험이      │ ← 상단 고정 타이틀 (흰색, 영상 내내)
│   성공한다면        │
├──────────────────┤
│      (친구)        │ ← 라벨·드립 (보라, 영상 상단 안쪽)
│                  │
│     원본 영상       │
│                  │
│  자, 이거 봐봐.      │ ← 화자 대사 (화자별 색, 영상 하단 안쪽)
├──────────────────┤
│  이 실험, 세 번째…   │ ← 나레이션 (주황, 아래 검은 띠)
│   © 채널명         │ ← 출처 표기
└──────────────────┘
```

- **화자 대사** — 이름을 붙이지 않고 **화자별로 색을 달리** 한다
  (노랑 → 흰색 → 하늘 → 연두 순 자동 배정).
- **긴 대사는 2~3어절씩 끊어서** 여러 자막으로 넘긴다. 단어 타임스탬프가 없어도
  글자 수에 비례해 시간을 나누고, 너무 잘게 쪼개져 스쳐 지나가지 않도록
  `SUBTITLE_MIN_DURATION` 을 만족할 만큼만 나눈다.
- **라벨/드립** — `(가위) (보자기)` 처럼 인물·상황을 찍어주거나 짧은 리액션을 친다.
- 자막은 검은 띠가 아니라 **영상 안쪽 가장자리**에 붙는다. 최종 프레임에서 영상이
  차지하는 세로 범위를 계산해 그 기준으로 위치를 잡는다.
- 프레임 정지가 삽입되면 시간축이 밀리므로, 모든 시각을
  `원본 클립 시간 → 최종 영상 시간` 으로 변환한 뒤 찍는다.

### ⑨ renderer — 최종 합성
1. 정지 삽입 구간 생성 후 concat
2. 나레이션 트랙을 `adelay` 로 배치하고, 원본은 `volume` 의 timeline `enable` 로 덕킹
3. 세로 리프레임 (원본을 블러 확대해 배경, 중앙에 원본 배치) — 끄면 원본 비율 유지
4. `ass` 필터로 자막 burn-in → `final.mp4` + 썸네일

---

## 2. 빠른 시작

### 준비물

| 항목 | 필수 | 비고 |
|---|---|---|
| Python 3.10+ / Node 18+ | ✅ | |
| ffmpeg (libass 포함) | ✅ | `brew install ffmpeg` / `sudo apt install ffmpeg` |
| LLM API 키 | ✅ | OpenAI 또는 Anthropic |
| Typecast API 키 | ⬜ | 선택. 기본 TTS 는 무료인 edge-tts 라 키가 필요 없습니다 |
| HuggingFace 토큰 | ⬜ | 화자분리(pyannote)를 쓸 때만 |
| 한글 폰트 | ⬜ | 없으면 시스템에서 자동 탐색 |

```bash
cd reaction-factory
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# 화자분리까지 쓰려면 (용량 큼, GPU 권장)
pip install -r backend/requirements-stt.txt

cp .env.example .env       # → OPENAI_API_KEY 등 채우기
cd frontend && npm install && cd ..
```

### 실행

```bash
./run.sh
# 또는 따로:
#   cd backend && uvicorn app.main:app --reload --port 8000
#   cd frontend && npm run dev
```

**http://localhost:5173** 접속. (프론트를 `npm run build` 해 두면
`http://localhost:8000` 하나로도 됩니다.)

---

## 3. 화면 사용법

1. 왼쪽에 **영상 URL**, 번역 말투, 나레이터 캐릭터, 드립 강도, 클립 길이를 넣고 시작.
2. **구간 탭**에 '가장 많이 다시 본' 곡선이 그려지고 후보 구간이 겹쳐 표시된다.
   후보를 클릭하거나 그래프를 드래그해 직접 지정한 뒤 **이 구간으로 확정**.
3. **대사·번역 탭** — 화자별 원문과 번역을 나란히 확인.
4. **연출 탭** — 대사 / 나레이션 / 드립 3트랙 타임라인. 나레이션마다
   `덕킹` / `정지 삽입` 배지가 붙는다.
5. **결과 탭** — 완성 영상 미리보기 + mp4 / SRT / ASS / 썸네일 다운로드 + 업로드 메타.

산출물은 `output/<job_id>/` 에 남는다.

```
output/<job_id>/
├── final.mp4        최종 영상
├── clip.mp4         자른 원본 구간
├── subtitles.ass    3종 스타일 자막 (편집기에서 수정 가능)
├── subtitles.srt    대사 + 나레이션 통합 자막
├── thumbnail.jpg
└── narration/       나레이션 음성 wav
```

> ASS 를 Aegisub 등에서 손보고 다시 굽고 싶다면
> `ffmpeg -i clip.mp4 -vf "ass=subtitles.ass" out.mp4` 로 바로 재렌더할 수 있습니다.

---

## 4. 환경변수 핵심

```bash
LLM_MODEL=openai:gpt-4o-mini
LLM_WRITER_MODEL=                 # 번역·드립만 큰 모델로 쓰고 싶을 때
OPENAI_API_KEY=

STT_PROVIDER=subtitles            # whisperx | faster-whisper | subtitles
WHISPER_MODEL=large-v3
HF_TOKEN=                         # 화자분리용

TTS_PROVIDER=edge                 # edge(무료, 기본) | typecast | none
EDGE_VOICE=ko-KR-InJoonNeural     # /api/voices 로 전체 목록 조회
EDGE_RATE=0                       # 말속도 % / EDGE_PITCH(Hz) / EDGE_VOLUME(%)

NARRATION_MODE=auto               # duck | freeze | auto
DUCK_LEVEL=0.25
MAX_NARRATIONS=6
MAX_GAGS=8

VERTICAL_REFRAME=true             # 1080x1920 세로 변환
SUBTITLE_SIZE=106
REVIEW_CLIP_SELECTION=true        # 구간 선택 게이트

YTDLP_COOKIES_FROM_BROWSER=       # 로그인 필요한 영상: chrome / firefox / edge
```

전체 목록과 설명은 `.env.example` 에 있습니다.

### 다운로드가 느릴 때 — 왜 느려지는가

**핵심: 유튜브에서 `download_ranges`(구간만 받기)를 쓰면 느려집니다.** 다른
프로젝트에서 yt-dlp 가 빨랐다면 그쪽은 구간 지정을 안 했기 때문입니다.

yt-dlp 소스를 보면 이유가 명확합니다.

```python
# yt_dlp/downloader/__init__.py:89  — 다른 어떤 판단보다 먼저
if (info_dict.get('section_start') or info_dict.get('section_end')) and FFmpegFD.can_download(...):
    return FFmpegFD          # ← 네이티브 다운로더를 아예 건너뛴다
```

```python
# yt_dlp/extractor/youtube/_video.py:3599
fmt['downloader_options'] = {'http_chunk_size': CHUNK_SIZE}   # CHUNK_SIZE = 10 MiB
```

유튜브 추출기는 모든 포맷에 **10MiB 단위로 끊어 받으라**는 힌트를 붙입니다.
네이티브 다운로더(`HttpFD`)는 이걸 읽어 Range 요청을 쪼개 보내고, 이게 googlevideo
속도 제한을 피하는 우회로입니다. 그런데 `FFmpegFD` 는 `downloader_options` 중
`ffmpeg_args` 만 읽고 `http_chunk_size` 는 **무시**합니다. 결국 ffmpeg 가 한 번에
쭉 스트리밍하고, 유튜브 스로틀링에 그대로 걸립니다.

여기에 `force_keyframes_at_cuts` 재인코딩까지 겹치면 30초 클립이 수백 초 걸립니다.

**그래서 기본 동작을 바꿨습니다** — `DOWNLOAD_SECTIONS_ONLY=false` 가 기본입니다.
전체를 네이티브 다운로더로 빠르게 받고 로컬에서 정확히 잘라냅니다.
`SECTION_DOWNLOAD_OVER_MINUTES=45` 를 넘는 긴 영상만 구간 다운로드로 자동 전환합니다.

그 밖에 적용된 것:

- **h264(avc1) 우선** 포맷 선택. AV1/VP9 은 디코딩이 몇 배 느려 컷·합성까지 느려집니다.
- 구간 다운로드를 쓸 때는 재인코딩 프리셋을 `veryfast` 로 넘깁니다 (yt-dlp 기본은 `medium`).
- heatmap 이 없어 오디오 분석이 필요할 때 **오디오만** 받습니다.
- 다운로드 중 6초마다 경과 시간·받은 용량을 로그에 찍습니다.

**그래도 느리면**

1. `MAX_HEIGHT=720` — 받을 용량이 절반 이하가 됩니다.
2. `YTDLP_FAST_CUT=true` — 구간 다운로드를 쓸 수밖에 없는 긴 영상에서, 재인코딩 없이
   스트림 복사로 잘라옵니다. 시작점이 키프레임까지 몇 초 앞당겨지므로
   `STT_PROVIDER=subtitles` 에서는 자막이 밀립니다. WhisperX 를 쓸 때만 켜세요.
3. `aria2c` 를 설치하고 `yt-dlp` 옵션에 `external_downloader='aria2c'` 를 넣으면
   전체 다운로드가 병렬로 더 빨라집니다 (`tools/source.py` 의 `_base_opts`).

---

### TTS 가 무음으로 나올 때

```bash
cd backend && python check_tts.py
```

`.env` 를 그대로 읽어서 provider 판정 → edge-tts 설치 여부 → Typecast 키 유효성과
보이스 목록(`voice_id`) → 실제 한 문장 합성까지 확인하고, **결과 wav 의 음량을 재서
무음인지 판정**한다. 무압축 wav 는 무음이어도 용량이 커서 파일 크기로는 못 거른다.

자주 나오는 원인:

| 증상 | 원인 | 해결 |
|---|---|---|
| `edge-tts 실패 → 무음 트랙으로 대체` (이유 없이) | edge-tts 미설치 | `pip install edge-tts` |
| `TYPECAST_VOICE_ID 가 비어 있습니다` | 보이스 id 미설정 | `check_tts.py` 로 목록을 뽑아 `tc_` 로 시작하는 id 를 넣기 |
| `Typecast 401/403` | 키가 틀렸거나 만료 | 키 재발급 |
| `Typecast 422` | 요청 값이 스펙 밖 | 응답 본문의 `message` 확인 |

## 5. 테스트

LLM·STT 없이 전체 흐름을 검증한다. 하이라이트 탐색·TTS·자막·ffmpeg 합성은
실제로 돌아가서 `final.mp4` 까지 만든다.

```bash
cd backend && python -m tests.test_pipeline
```

```
✓ 구간 선택 게이트 진입 — 후보 2개 (전략=audio)
✓ resume 후 끝까지 완주
✓ 번역 8줄 · 화자 2명
✓ 나레이션 3개 (모드: duck) +0초
✓ 자막 ASS: 대사 8 / 나레이션 3 / 드립 3 (1080x1920)
✓ 최종 영상: output/xxxx/final.mp4 (50.0초 / 15.8MB / 1080x1920)
모든 스모크 테스트 통과
```

로컬 파일 경로를 URL 자리에 넣으면 yt-dlp 없이도 파이프라인이 전부 돕니다
(오프라인 개발용).

---

## 6. API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/health` | 키·설정 상태 |
| GET | `/api/graph` | 노드 목록 + mermaid |
| GET | `/api/voices` | Typecast/edge 보이스 목록 |
| POST | `/api/jobs` | 잡 생성·실행 |
| GET | `/api/jobs/{id}/stream` | **SSE** 실시간 이벤트 |
| POST | `/api/jobs/{id}/clip` | 구간 확정 후 그래프 재개 |
| POST | `/api/jobs/{id}/cancel` | 중단 |
| GET | `/api/artifacts/{id}/{file}` | mp4 / ass / srt / 썸네일 |

SSE `kind`: `status` `node` `log` `source` `candidates` `clip_selection_required`
`clip` `transcript` `translated` `script` `narration` `subtitle` `render` `result`

---

## 7. 구조

```
reaction-factory/
├── backend/app/
│   ├── main.py          FastAPI + SSE
│   ├── runner.py        잡 실행 / interrupt 재개
│   ├── events.py        contextvar 기반 이벤트 버스
│   ├── config.py        .env
│   ├── schemas.py       구조화 출력 스키마
│   ├── graph/
│   │   ├── builder.py   StateGraph
│   │   ├── state.py     공유 상태
│   │   └── nodes/       9개 에이전트
│   └── tools/
│       ├── source.py    yt-dlp (메타·heatmap·자막·구간 다운로드)
│       ├── highlight.py heatmap / 오디오 에너지 → 후보 구간
│       ├── stt.py       WhisperX / faster-whisper / 자막
│       ├── tts.py       Typecast / edge-tts
│       ├── subtitle.py  ASS 3종 스타일
│       ├── render.py    정지삽입 · 덕킹 · 리프레임 · burn-in
│       ├── media.py     ffmpeg/ffprobe 공통
│       └── fonts.py     한글 폰트 탐색
├── frontend/            React + Vite (히트맵 차트 · 타임라인)
└── output/
```

### 확장 포인트

- **내 채널 Analytics 붙이기** — `tools/highlight.py` 에 `from_analytics()` 추가
  (`youtubeAnalytics.reports.query`, `dimensions=elapsedVideoTimeRatio`,
  `metrics=audienceWatchRatio`). 반환 형식만 `(t, score)` 곡선으로 맞추면 끝.
- **효과음 태그** — `tools/stt.py` 에 ElevenLabs Scribe 같은 audio-event 태깅
  백엔드를 추가하면 `(웃음)` `(박수)` 를 자동으로 자막에 넣을 수 있다.
- **여러 클립 동시 생산** — `runner.create_job` 을 후보 개수만큼 돌리고
  `clip_gate` 를 끄면 한 영상에서 클립 N개를 병렬로 뽑을 수 있다.
- **잡 이력 영속화** — `InMemorySaver` → `SqliteSaver`.

---

## 8. 주의

- **저작권.** 남의 영상을 잘라 재가공하는 파이프라인입니다. 원저작자 허락,
  출처 표기, 변형 정도(해설·번역·비평의 비중)에 따라 합법성이 갈립니다.
  자동 업로드를 붙이지 않은 이유이기도 합니다 — 올리기 전에 사람이 판단하세요.
- yt-dlp 로 다운로드하는 행위 자체가 유튜브 서비스 약관에 저촉될 수 있습니다.
- **드립 자막**은 LLM 이 씁니다. 인신공격·비하는 프롬프트로 막아 두었지만
  완전하지 않으니 업로드 전에 `연출 탭`에서 눈으로 확인하세요.
- 화자분리(pyannote)는 HuggingFace 에서 모델 약관 동의가 필요합니다.
