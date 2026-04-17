"""SearchBackend ABC — 所有搜索后端的统一接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class SearchBackend(ABC):
    """搜索后端抽象基类。

    每个后端的 search() 必须返回统一格式：
        [{"url": str, "title": str, "snippet": str, "score": float, "source": str}]
    """

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        ...
