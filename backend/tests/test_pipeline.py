"""LLM·STT 없이 전체 파이프라인을 검증하는 스모크 테스트.

  cd backend && python -m tests.test_pipeline

LLM 호출과 STT 는 가짜로 대체하고, 하이라이트 탐색·TTS·자막·ffmpeg 합성은
전부 실제로 돌려서 final.mp4 까지 만든다.
구간 선택 게이트의 interrupt → resume 도 함께 확인한다.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import events, runner  # noqa: E402
from app.schemas import (  # noqa: E402
    CandidateRanking, ClipScript, Gag, Narration, RunRequest, SpeakerMap,
    SpeakerName, TranslatedLine, TranslationResult,
)

TEST_VIDEO = Path("/tmp/rf_testsrc.mp4")

# 클립(30~90초 구간) 안에서의 상대 시각 기준 가짜 대사.
# 중간중간 일부러 빈 구간을 넉넉히 남겨 나레이션이 들어갈 자리를 만든다.
FAKE_UTTERANCES = [
    (1.0, 3.4, "SPEAKER_00", "Alright, watch this."),
    (3.8, 6.0, "SPEAKER_01", "There's no way that works."),
    (11.0, 13.6, "SPEAKER_00", "I told you it would."),
    (14.0, 16.2, "SPEAKER_01", "Okay that was actually insane."),
    (24.0, 26.5, "SPEAKER_00", "And we're not even done yet."),
    (27.0, 29.0, "SPEAKER_01", "Please stop."),
    (38.0, 40.4, "SPEAKER_00", "Last one, I promise."),
    (41.0, 43.0, "SPEAKER_01", "You said that twice already."),
]

KO = {
    0: "자, 이거 봐봐.",
    1: "저게 될 리가 없지.",
    2: "될 거라고 했잖아.",
    3: "방금 건 진짜 미쳤다.",
    4: "아직 안 끝났어.",
    5: "제발 그만해.",
    6: "마지막이야, 약속.",
    7: "그 말 벌써 두 번째야.",
}

CALLS: list[str] = []


def make_video() -> None:
    if TEST_VIDEO.exists():
        return
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=90",
        "-f", "lavfi", "-i",
        "sine=frequency=220:duration=90,volume='0.12+0.7*between(t,30,52)':eval=frame",
        "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(TEST_VIDEO),
    ], check=True)


async def fake_structured(schema, system, user, **kw):
    CALLS.append(schema.__name__)
    if schema is CandidateRanking:
        return CandidateRanking(chosen_index=0, reason="에너지가 가장 크게 튀는 구간",
                                hook_guess="반전이 터지는 순간")
    if schema is SpeakerMap:
        return SpeakerMap(
            speakers=[SpeakerName(speaker_id="SPEAKER_00", name="진행자"),
                      SpeakerName(speaker_id="SPEAKER_01", name="친구")],
            summary="두 사람이 무모한 실험을 반복하며 서로를 놀리는 장면이다.",
        )
    if schema is TranslationResult:
        return TranslationResult(
            lines=[TranslatedLine(index=i, ko=v) for i, v in KO.items()]
        )
    if schema is ClipScript:
        return ClipScript(
            context="두 사람이 무모한 실험을 이어가는 클립.",
            overlay_title="무모한 실험이 성공한다면",
            narrations=[
                Narration(start=7.0, text="이 실험, 이미 세 번째 시도입니다.", emotion="normal"),
                Narration(start=18.0, text="여기서부터 분위기가 완전히 바뀝니다.", emotion="normal"),
                Narration(start=31.0, text="사실 이 장면이 조회수의 절반을 만들었습니다.",
                          emotion="normal"),
            ],
            gags=[
                Gag(start=5.0, duration=1.8, text="(진행자)"),
                Gag(start=14.5, duration=2.0, text="(친구)"),
                Gag(start=42.0, duration=1.8, text="약속은 지키라고 있는 건데"),
            ],
            title="세 번째 시도 만에 터진 장면",
            description="무모한 실험이 결국 성공하는 순간.",
            tags=["실험", "리액션", "해외영상"],
        )
    raise AssertionError(f"unexpected schema {schema}")


async def fake_transcribe(audio_path, **kw):
    segs = [
        {"index": i, "start": s, "end": e, "speaker": sp, "text": t, "kind": "speech"}
        for i, (s, e, sp, t) in enumerate(FAKE_UTTERANCES)
    ]
    return segs, "en", ["테스트용 가짜 STT"]


def patch() -> None:
    from app.tools import llm as llm_mod
    from app.tools import stt as stt_mod

    llm_mod.structured = fake_structured
    stt_mod.transcribe = fake_transcribe
    for name in ("highlighter", "transcriber", "translator", "scripter"):
        mod = __import__(f"app.graph.nodes.{name}", fromlist=["x"])
        if hasattr(mod, "structured"):
            mod.structured = fake_structured
    tr = __import__("app.graph.nodes.transcriber", fromlist=["x"])
    tr.stt = stt_mod


async def main() -> int:
    make_video()
    patch()

    job = runner.create_job(RunRequest(
        url=str(TEST_VIDEO),
        translation_style="반말 위주, 인터넷 방송 자막 톤",
        narration_persona="무심한 다큐 나레이터",
        gag_level=2,
        clip_seconds=50,
        narration_mode="auto",
        vertical_reframe=True,
        review_clip_selection=True,
    ))

    seen: list[dict] = []

    async def collect():
        async for ev in events.subscribe(job.id):
            seen.append(ev)

    collector = asyncio.create_task(collect())
    runner.start(job)
    await job.task

    assert job.status == "waiting_clip", f"기대 waiting_clip, 실제 {job.status} / {job.error}"
    cands = job.result.get("candidates", [])
    assert cands, "후보 구간이 비었습니다"
    print(f"✓ 구간 선택 게이트 진입 — 후보 {len(cands)}개 "
          f"(전략={job.result.get('strategy')})")
    for c in cands:
        print(f"    #{c['index']} {c['start']:.1f}~{c['end']:.1f}s  score={c['score']:.3f}")

    runner.resume_clip(job, {"candidate_index": 0})
    await job.task
    assert job.status == "done", f"기대 done, 실제 {job.status}\n{job.error}"
    print("✓ resume 후 끝까지 완주")

    await asyncio.sleep(0.1)
    collector.cancel()

    st = job.result
    assert not st.get("errors"), st["errors"]

    clip = st["clip"]
    assert Path(clip["path"]).exists()
    print(f"✓ 클립: {clip['duration']}초 {clip['width']}x{clip['height']}")

    tr = st["translated"]
    assert len(tr) == len(FAKE_UTTERANCES)
    assert all(l.get("ko") for l in tr), "번역 누락"
    print(f"✓ 번역 {len(tr)}줄 · 화자 {len(st['speaker_map'])}명")

    slots = st["narration_slots"]
    assert slots, "나레이션 슬롯 없음"
    modes = {s["mode"] for s in slots}
    print(f"✓ 나레이션 {len(slots)}개 (모드: {', '.join(sorted(modes))}) "
          f"+{st['timeline']['added']}초")

    sub = st["subtitle"]
    ass = Path(sub["ass"])
    assert ass.exists()
    body = ass.read_text(encoding="utf-8")
    for style in ("Style: Dia0", "Style: Dia1", "Style: Label",
                  "Style: Title", "Style: Narration", "Style: Credit"):
        assert style in body, f"{style} 누락"
    for tag in (",Narration,,", ",Label,,", ",Title,,", ",Credit,,", ",Dia0,,", ",Dia1,,"):
        assert tag in body, f"{tag} 이벤트가 안 찍혔습니다"
    # 화자마다 다른 색이 배정됐는지
    c0 = [l for l in body.splitlines() if l.startswith("Style: Dia0")][0].split(",")[3]
    c1 = [l for l in body.splitlines() if l.startswith("Style: Dia1")][0].split(",")[3]
    assert c0 != c1, f"화자 색이 같습니다: {c0}"
    print(f"✓ 화자 색 구분: SPEAKER_00={c0} / SPEAKER_01={c1}")
    assert sub["video_top"] > 0 and sub["video_bottom"] < sub["out_h"], "레터박스 영역 계산 실패"
    print(f"✓ 레터박스: 영상 y={sub['video_top']}~{sub['video_bottom']} / "
          f"타이틀='{sub['title']}' 크레딧='{sub['credit']}'")
    print(f"✓ 자막 ASS: 대사 {sub['counts']['dialogue']} / "
          f"나레이션 {sub['counts']['narration']} / 드립 {sub['counts']['gag']} "
          f"({sub['out_w']}x{sub['out_h']}, {sub['font']})")

    r = st["render"]
    assert Path(r["path"]).exists()
    assert r["width"] == sub["out_w"] and r["height"] == sub["out_h"]
    expected = clip["duration"] + st["timeline"]["added"]
    assert abs(r["duration"] - expected) < 2.5, \
        f"길이 불일치: 결과 {r['duration']} vs 예상 {expected}"
    print(f"✓ 최종 영상: {r['path']} ({r['duration']}초 / {r['size_mb']}MB / "
          f"{r['width']}x{r['height']})")

    kinds = {e["kind"] for e in seen}
    for need in ("node", "log", "source", "candidates", "clip_selection_required",
                 "clip", "transcript", "translated", "script", "narration",
                 "subtitle", "render", "status"):
        assert need in kinds, f"이벤트 누락: {need}"
    print(f"✓ 이벤트 {len(seen)}건 / 종류 {len(kinds)}가지")
    print(f"✓ LLM 호출: {CALLS}")
    print("\n모든 스모크 테스트 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
