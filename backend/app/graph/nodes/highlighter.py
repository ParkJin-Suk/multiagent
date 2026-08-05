"""② 하이라이트 추출 에이전트.

heatmap(가장 많이 다시 본 장면) → 없으면 오디오 에너지로 후보 구간을 만들고,
자막이 있으면 LLM 이 그 구간들의 대사를 읽고 최종 1개를 고른다.
"""
from __future__ import annotations

from pathlib import Path

from ...config import settings
from ...events import emit, log, node_status
from ...schemas import CandidateRanking
from ...tools import highlight
from ...tools.llm import structured
from ..state import ClipState

NAME = "highlighter"

SYSTEM = """당신은 리액션·해설 채널의 편집자다.
후보 구간들의 대사 미리보기를 보고, 짧은 클립으로 잘랐을 때 가장 재밌을 구간을 고른다.

기준:
- 사건이 '터지는' 순간이 구간 안에 들어 있는가 (반전, 실수, 폭소, 결과 발표).
- 앞뒤 맥락 없이도 이해되는가.
- 대사가 너무 없거나(정적) 너무 빽빽하지(정보 과잉) 않은가.
지표 점수가 높아도 대사가 밋밋하면 낮게 본다. 한국어로 답하라."""


async def highlighter(state: ClipState) -> dict:
    node_status(NAME, "running")
    cfg = state.get("config", {})
    src = state.get("source", {})
    duration = float(src.get("duration") or 0)

    try:
        # 사용자가 구간을 직접 지정한 경우
        if cfg.get("manual_start") is not None and cfg.get("manual_end") is not None:
            chosen = {"start": float(cfg["manual_start"]), "end": float(cfg["manual_end"])}
            cand = [{"index": 0, **chosen, "score": 1.0, "source": "manual",
                     "reason": "사용자 지정 구간"}]
            emit("candidates", candidates=cand, curve=[], strategy="manual", chosen=chosen)
            node_status(NAME, "done", strategy="manual", count=1)
            return {"candidates": cand, "curve": [], "strategy": "manual", "chosen": chosen}

        clip_len = float(cfg.get("clip_seconds") or 0) or (
            (settings.clip_min_seconds + settings.clip_max_seconds) / 2
        )
        audio_path = Path(src["audio_path"]) if src.get("audio_path") else None

        raw, curve, strategy = await highlight.find_candidates(
            heatmap=state.get("heatmap") or None,
            audio_path=audio_path,
            duration=duration,
            clip_len=clip_len,
            count=int(cfg.get("candidate_count") or settings.candidate_count),
            strategy=cfg.get("highlight_strategy"),
        )
        cands = [{"index": i, **c} for i, c in enumerate(raw)]

        # 자막이 있으면 각 후보의 대사를 붙여서 LLM 에게 고르게 한다
        subs = state.get("subtitle_segments") or []
        chosen_index = 0
        if len(cands) > 1 and subs:
            preview = []
            for c in cands:
                lines = [
                    s["text"] for s in subs
                    if s["end"] > c["start"] and s["start"] < c["end"]
                ][:14]
                c["preview"] = " / ".join(lines)[:600]
                preview.append(
                    f"[{c['index']}] {c['start']:.0f}~{c['end']:.0f}초 "
                    f"(지표 {c['score']:.2f})\n{c['preview'] or '(대사 없음)'}"
                )
            try:
                ranking: CandidateRanking = await structured(
                    CandidateRanking, SYSTEM,
                    f"영상 제목: {src.get('title')}\n\n[후보 구간]\n" + "\n\n".join(preview),
                    temperature=0.3,
                )
                chosen_index = max(0, min(ranking.chosen_index, len(cands) - 1))
                cands[chosen_index]["reason"] = ranking.reason or cands[chosen_index]["reason"]
                cands[chosen_index]["hook_guess"] = ranking.hook_guess
                log(f"LLM 이 후보 {chosen_index}번 선택: {ranking.reason}", node=NAME)
            except Exception as exc:  # noqa: BLE001
                log(f"후보 재정렬 실패({exc}) — 지표 1순위를 사용합니다.", level="warn", node=NAME)

        chosen = {"start": cands[chosen_index]["start"], "end": cands[chosen_index]["end"],
                  "index": chosen_index}
        emit("candidates", candidates=cands, curve=curve, strategy=strategy, chosen=chosen)
        log(f"{strategy} 전략 · 후보 {len(cands)}개 · 기본 선택 "
            f"{chosen['start']:.0f}~{chosen['end']:.0f}초", node=NAME)
        node_status(NAME, "done", strategy=strategy, count=len(cands),
                    chosen=f"{chosen['start']:.0f}~{chosen['end']:.0f}s")
        return {"candidates": cands, "curve": curve, "strategy": strategy, "chosen": chosen}
    except Exception as exc:  # noqa: BLE001
        node_status(NAME, "error", message=str(exc))
        return {"errors": [f"[highlighter] {exc}"]}
