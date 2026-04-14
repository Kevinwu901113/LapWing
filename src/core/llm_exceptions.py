"""LLM 调用异常类型 — 供 tool loop 恢复逻辑使用。"""


class LLMError(Exception):
    """LLM 调用基础异常。"""
    pass


class PromptTooLongError(LLMError):
    """Prompt 超过模型上下文限制。"""
    pass


class EmptyResponseError(LLMError):
    """模型返回空内容。"""
    pass


class TruncatedResponseError(LLMError):
    """模型输出被截断（max_tokens 不够）。"""
    pass


class APIOverloadError(LLMError):
    """API 过载（529/503）。"""
    pass


class APITimeoutError(LLMError):
    """API 超时。"""
    pass


class APIConnectionError(LLMError):
    """API 连接失败。"""
    pass


def classify_as_llm_exception(exc: Exception) -> LLMError | None:
    """将原始 provider 异常映射为 LLM 异常类型。返回 None 表示不可恢复。"""
    class_name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)

    # Prompt too long
    if status_code == 400 and any(
        kw in message for kw in ("too long", "context", "maximum", "token limit", "max_tokens")
    ):
        return PromptTooLongError(str(exc))

    # API overload
    if status_code in {429, 503, 529} or "ratelimit" in class_name or "rate limit" in message:
        return APIOverloadError(str(exc))

    # Timeout
    if "timeout" in class_name or "timed out" in message:
        return APITimeoutError(str(exc))

    # Connection error
    if "connect" in class_name or "connection" in message:
        return APIConnectionError(str(exc))

    return None
