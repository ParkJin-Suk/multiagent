"""잡 실행기 — 그래프를 백그라운드로 돌리고 상태를 관리한다."""
from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from . import events
from .config import settings
from .graph.builder import get_graph
from .graph.state import new_state
from .schemas import RunRequest


@dataclass
class Job:
    id: str
    request: dict[str, Any]
    # pending | running | waiting_clip | done | error | cancelled
    status: str = "pending"
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    task: asyncio.Task | None = None


JOBS: dict[str, Job] = {}


def _config(job_id: str) -> dict:
    return {"configurable": {"thread_id": job_id}, "recursion_limit": 50}


def create_job(req: RunRequest) -> Job:
    job_id = uuid.uuid4().hex[:12]
    cfg = req.model_dump()
    if cfg.get("review_clip_selection") is None:
        cfg["review_clip_selection"] = settings.review_clip_selection
    if cfg.get("vertical_reframe") is None:
        cfg["vertical_reframe"] = settings.vertical_reframe
    if not cfg.get("narration_mode"):
        cfg["narration_mode"] = settings.narration_mode
    if not cfg.get("translation_style"):
        cfg["translation_style"] = settings.translation_style

    job = Job(id=job_id, request=cfg)
    JOBS[job_id] = job
    events.register(job_id)
    return job


async def _drive(job: Job, payload: Any) -> None:
    events.bind_job(job.id)
    graph = get_graph()
    cfg = _config(job.id)
    job.status = "running"
    events.emit("status", status="running")

    latest: dict[str, Any] = {}
    try:
        async for chunk in graph.astream(payload, config=cfg, stream_mode="values"):
            if isinstance(chunk, dict):
                latest = chunk

        snapshot = await graph.aget_state(cfg)
        if snapshot.next:                      # interrupt (구간 선택 대기)
            job.status = "waiting_clip"
            job.result = dict(snapshot.values or latest)
            events.emit("status", status="waiting_clip", next=list(snapshot.next))
            return

        job.result = dict(snapshot.values or latest)
        job.status = "done"
        events.emit("status", status="done", summary=_summary(job.result))
        events.emit("result", state=_public_state(job.result))
    except asyncio.CancelledError:
        job.status = "cancelled"
        events.emit("status", status="cancelled")
        raise
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = f"{exc}\n{traceback.format_exc(limit=3)}"
        events.emit("status", status="error", message=str(exc))
        events.log(f"실행 실패: {exc}", level="error")
    finally:
        if job.status in ("done", "error", "cancelled"):
            events.close(job.id)


def start(job: Job) -> None:
    job.task = asyncio.create_task(_drive(job, new_state(job.id, job.request)))


def resume_clip(job: Job, decision: dict[str, Any]) -> None:
    job.task = asyncio.create_task(_drive(job, Command(resume=decision)))


async def cancel(job: Job) -> None:
    if job.task and not job.task.done():
        job.task.cancel()
        try:
            await job.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    events.close(job.id)


def _summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": state.get("script", {}).get("title") or state.get("source", {}).get("title"),
        "clip": state.get("chosen", {}),
        "render": state.get("render", {}),
        "narrations": len(state.get("narration_slots", [])),
        "gags": len(state.get("script", {}).get("gags", [])),
        "errors": state.get("errors", []),
    }


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    out.pop("heatmap", None)
    out.pop("subtitle_segments", None)
    out["transcript"] = (out.get("transcript") or [])[:400]
    out["translated"] = (out.get("translated") or [])[:400]
    sub = dict(out.get("subtitle") or {})
    sub.pop("events", None)
    if sub:
        out["subtitle"] = sub
    return out


def public_view(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "status": job.status,
        "request": job.request,
        "error": job.error,
        "state": _public_state(job.result) if job.result else {},
    }
