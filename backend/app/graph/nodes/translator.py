"""⑤ 번역 에이전트 — 말투를 지정해 화자별로 일관되게 옮긴다."""
from __future__ import annotations

import asyncio

from ...config import settings
from ...events import emit, log, node_status
from ...schemas import TranslationResult
from ...tools.llm import structured
from ..state import ClipState

NAME = "translator"
BATCH = 40

SYSTEM = """당신은 해외 영상을 한국 쇼츠 자막으로 옮기는 번역가다.
번역기가 아니다. 원문의 '뜻'이 아니라 '그 상황에서 한국 사람이 실제로 뱉을 말'을 쓴다.

가장 중요한 규칙 — 번역투를 지워라:
- 직역해서 어색하면 문장을 통째로 갈아엎어라. 원문 구조를 따라갈 의무가 없다.
- 다음은 전부 금지: "그것은", "~라고 생각한다", "정말로", "매우", "~하는 것이다",
  "나의/너의" 같은 불필요한 소유격, 주어 남발("나는", "너는").
  한국어 구어는 주어를 거의 생략한다.
- 영어 관용구는 뜻만 가져와 한국식 표현으로 바꾼다.
- 감탄사·욕·말더듬·비명은 살린다. 이게 자막의 맛이다. (수위는 지정된 말투를 따른다)

예시 (왼쪽처럼 쓰지 마라 → 오른쪽처럼 써라):
- "그것은 작동하지 않을 것이다" → "저거 안 될걸"
- "당신은 나가고 싶습니까?" → "나가고 싶어?"
- "나는 너를 파괴할 것이다, 애송이" → "널 끝장내주마, 애송이"
- "이것은 정말로 미쳤다" → "방금 건 진짜 미쳤다"
- "잠깐만 기다려 주세요" → "잠깐만"
- "오 마이 갓" → "헐" / "미쳤다"

그 밖에:
- 짧게. 화면에 뜨는 자막이라 길면 읽히지 않는다. 주어진 최대 글자 수를 넘기지 마라.
- 화자마다 말투를 일관되게 유지한다 (같은 화자가 존댓말↔반말을 오가지 않게).
- 원문에 없는 정보를 지어내지 않는다. 짧게 줄이는 건 되지만 덧붙이는 건 안 된다.
- 대사가 아닌 소리는 (웃음) (박수) (비명) 처럼 괄호로 적는다.
- 입력의 index 를 그대로 유지하고, 모든 index 에 한 줄씩 돌려준다."""


def _limit(seg: dict) -> int:
    """발화 길이에 맞춘 자막 글자 수 상한. 한국어 자막은 초당 6~7자가 한계."""
    span = max(0.6, float(seg.get("end", 0)) - float(seg.get("start", 0)))
    return max(6, min(40, int(span * 7)))


async def translator(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    segments = state.get("transcript", [])
    if not segments:
        node_status(NAME, "skipped", message="대사 없음")
        return {"translated": []}

    style = (cfg.get("translation_style") or settings.translation_style).strip()
    speaker_map = state.get("speaker_map", {})

    header = f"""[상황 요약]
{state.get('speaker_summary') or '(요약 없음)'}

[원본 언어] {state.get('language') or '자동감지'}
[화자 호칭] {', '.join(f'{k}={v}' for k, v in speaker_map.items()) or '(단일 화자)'}

[말투 지시]
{style}"""

    try:
        batches = [segments[i:i + BATCH] for i in range(0, len(segments), BATCH)]
        log(f"{len(segments)}줄을 {len(batches)}배치로 번역", node=NAME)

        results = await asyncio.gather(*[
            structured(
                TranslationResult, SYSTEM,
                header + "\n\n[번역할 대사]  index / 화자 / 최대 글자수 / 원문\n" + "\n".join(
                    f"{s['index']}\t[{speaker_map.get(s['speaker'], s['speaker'])}]\t"
                    f"{_limit(s)}자\t{s['text']}"
                    for s in batch
                ),
                model=settings.writer_model, temperature=0.4,
            )
            for batch in batches
        ], return_exceptions=True)

        ko_by_index: dict[int, str] = {}
        for r in results:
            if isinstance(r, Exception):
                log(f"배치 번역 실패: {r}", level="warn", node=NAME)
                continue
            for line in r.lines:
                ko_by_index[line.index] = line.ko.strip()

        translated = []
        missing = 0
        for s in segments:
            ko = ko_by_index.get(s["index"], "")
            if not ko:
                missing += 1
                ko = s["text"]      # 번역 누락 시 원문 유지
            translated.append({**s, "ko": ko})

        if missing:
            log(f"{missing}줄은 번역이 비어 원문을 그대로 씁니다.", level="warn", node=NAME)

        emit("translated", translated=translated, style=style)
        node_status(NAME, "done", lines=len(translated), missing=missing)
        return {"translated": translated}
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[translator] {exc}"]}
