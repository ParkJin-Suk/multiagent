"""잡(job) 단위 이벤트 버스.

그래프 노드가 어디서든 `emit()` 을 호출하면, 해당 잡의 SSE 스트림으로
실시간 전달된다. 노드가 job_id 를 인자로 들고 다니지 않아도 되도록
contextvar 로 현재 잡을 추적한다.
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any, AsyncIterator

_current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_job", default=None
)

# job_id → (구독 큐 목록, 지금까지의 이벤트 로그)
_queues: dict[str, list[asyncio.Queue]] = {}
_history: dict[str, list[dict[str, Any]]] = {}

_SENTINEL = {"__end__": True}


def bind_job(job_id: str) -> None:
    """현재 실행 컨텍스트를 특정 잡에 묶는다."""
    _current_job.set(job_id)


def current_job() -> str | None:
    return _current_job.get()


def register(job_id: str) -> None:
    _queues.setdefault(job_id, [])
    _history.setdefault(job_id, [])


def history(job_id: str) -> list[dict[str, Any]]:
    return list(_history.get(job_id, []))


def emit(kind: str, **payload: Any) -> None:
    """이벤트 발행. 동기 함수라 노드 어디서든 부담 없이 호출 가능."""
    job_id = payload.pop("job_id", None) or current_job()
    if job_id is None:
        return
    event = {"ts": time.time(), "kind": kind, **payload}
    _history.setdefault(job_id, []).append(event)
    for q in list(_queues.get(job_id, [])):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover
            pass


def node_status(node: str, status: str, **extra: Any) -> None:
    """노드 상태 전용 헬퍼. status: running | done | error | skipped"""
    emit("node", node=node, status=status, **extra)


def log(message: str, level: str = "info", node: str | None = None) -> None:
    emit("log", level=level, message=message, node=node or "")


def close(job_id: str) -> None:
    for q in list(_queues.get(job_id, [])):
        try:
            q.put_nowait(dict(_SENTINEL))
        except asyncio.QueueFull:  # pragma: no cover
            pass


async def subscribe(job_id: str, replay: bool = True) -> AsyncIterator[dict[str, Any]]:
    """SSE 스트림용 비동기 이터레이터. 구독 시점 이전 이벤트도 재생한다."""
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    _queues.setdefault(job_id, []).append(q)
    try:
        if replay:
            for ev in list(_history.get(job_id, [])):
                yield ev
        while True:
            ev = await q.get()
            if ev.get("__end__"):
                return
            yield ev
    finally:
        try:
            _queues.get(job_id, []).remove(q)
        except ValueError:
            pass


def cleanup(job_id: str) -> None:
    _queues.pop(job_id, None)
    _history.pop(job_id, None)
