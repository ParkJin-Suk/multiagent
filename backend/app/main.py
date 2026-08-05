"""FastAPI 진입점."""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import events, runner
from .config import ROOT_DIR, settings
from .graph.builder import graph_mermaid
from .graph.state import NODE_DESCS, NODE_LABELS, NODE_ORDER
from .schemas import ClipDecision, RunRequest
from .tools.fonts import font_warning
from .tools.tts import list_voices

app = FastAPI(title="Reaction Factory", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────
#  메타
# ─────────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "llm_model": settings.llm_model,
        "llm_key": bool(settings.openai_api_key or settings.anthropic_api_key),
        "stt_provider": settings.stt_provider,
        "whisper_model": settings.whisper_model,
        "hf_token": bool(settings.hf_token),
        "tts_provider": settings.tts_provider,
        "typecast_key": bool(settings.typecast_api_key),
        "narration_mode": settings.narration_mode,
        "vertical_reframe": settings.vertical_reframe,
        "review_clip_selection": settings.review_clip_selection,
        "font_warning": font_warning(),
    }


@app.get("/api/graph")
async def graph_info():
    return {
        "nodes": [
            {"id": n, "label": NODE_LABELS[n], "desc": NODE_DESCS.get(n, "")}
            for n in NODE_ORDER
        ],
        "mermaid": graph_mermaid(),
    }


@app.get("/api/voices")
async def voices():
    return {
        "voices": await list_voices(),
        "provider": settings.tts_provider,
        "current": settings.typecast_voice_id or settings.edge_voice,
    }


# ─────────────────────────────────────────────────────────────────────
#  잡
# ─────────────────────────────────────────────────────────────────────
@app.post("/api/jobs")
async def create_job(req: RunRequest):
    if not (settings.openai_api_key or settings.anthropic_api_key):
        raise HTTPException(400, "LLM API 키가 없습니다. .env 에 OPENAI_API_KEY 등을 넣어주세요.")
    if not req.url.strip():
        raise HTTPException(400, "영상 URL 또는 로컬 파일 경로가 필요합니다.")
    job = runner.create_job(req)
    runner.start(job)
    return {"job_id": job.id, "status": job.status}


@app.get("/api/jobs")
async def list_jobs():
    return {
        "jobs": [
            {"id": j.id, "status": j.status,
             "title": j.result.get("source", {}).get("title", "")}
            for j in runner.JOBS.values()
        ]
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = runner.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return runner.public_view(job)


@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str):
    if job_id not in runner.JOBS:
        raise HTTPException(404, "job not found")

    async def gen():
        try:
            async for ev in events.subscribe(job_id):
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
        except asyncio.CancelledError:
            return
        yield 'data: {"kind":"eof"}\n\n'

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.post("/api/jobs/{job_id}/clip")
async def choose_clip(job_id: str, body: ClipDecision):
    """구간 선택 게이트 재개."""
    job = runner.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status != "waiting_clip":
        raise HTTPException(409, f"구간 선택 대기 상태가 아닙니다 (현재: {job.status})")
    runner.resume_clip(job, body.model_dump())
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    job = runner.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    await runner.cancel(job)
    return {"ok": True, "status": job.status}


# ─────────────────────────────────────────────────────────────────────
#  아티팩트
# ─────────────────────────────────────────────────────────────────────
@app.get("/api/artifacts/{job_id}/{filename}")
async def artifact(job_id: str, filename: str):
    if "/" in filename or ".." in filename or ".." in job_id:
        raise HTTPException(400, "bad path")
    path = settings.output_path / job_id / filename
    if not path.exists():
        raise HTTPException(404, "artifact not found")
    return FileResponse(path)


# ─────────────────────────────────────────────────────────────────────
#  프론트엔드
# ─────────────────────────────────────────────────────────────────────
DIST = ROOT_DIR / "frontend" / "dist"
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="ui")
else:
    @app.get("/")
    async def root():
        return {"message": "Reaction Factory API. frontend 에서 `npm run dev`.", "docs": "/docs"}
