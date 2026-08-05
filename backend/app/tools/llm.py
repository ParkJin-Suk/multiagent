"""LLM 팩토리 + 구조화 출력 헬퍼."""
from __future__ import annotations

from typing import Type, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from ..config import settings
from ..events import log

T = TypeVar("T", bound=BaseModel)

_cache: dict[str, BaseChatModel] = {}


def get_llm(model: str | None = None, temperature: float = 0.3) -> BaseChatModel:
    spec = model or settings.llm_model
    key = f"{spec}|{temperature}"
    if key not in _cache:
        _cache[key] = init_chat_model(spec, temperature=temperature)
    return _cache[key]


async def structured(
    schema: Type[T],
    system: str,
    user: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    retries: int = 2,
) -> T:
    """스키마에 맞는 객체를 뱉을 때까지 (최대 retries) 시도."""
    llm = get_llm(model, temperature).with_structured_output(schema)
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = await llm.ainvoke(messages)
            if result is None:
                raise ValueError("LLM 이 빈 응답을 반환했습니다.")
            return result  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log(f"구조화 출력 재시도 {attempt + 1}/{retries}: {exc}", level="warn")
    raise RuntimeError(f"구조화 출력 실패: {last_err}")
