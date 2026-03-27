"""Browser Agent - 访问指定网址并总结内容。"""

import logging
import re
from urllib.parse import urlparse

from src.agents.base import AgentResult, AgentTask, BaseAgent
from src.core.prompt_loader import load_prompt
from src.tools import web_fetcher

logger = logging.getLogger("lapwing.agents.browser")

_URL_PATTERN = re.compile(r"https?://[^\s]+")


class BrowserAgent(BaseAgent):
    name = "browser"
    description = "访问指定网页，阅读并总结页面内容"
    capabilities = ["浏览指定网址", "阅读网页文章", "提取网页信息"]

    def __init__(self, memory, knowledge_manager=None) -> None:
        self._memory = memory
        self._knowledge_manager = knowledge_manager

    async def execute(self, task: AgentTask, router) -> AgentResult:
        url = self._extract_url(task.user_message)
        if url is None:
            return AgentResult(
                content="请提供一个网址。",
                needs_persona_formatting=True,
            )

        fetch_result = await web_fetcher.fetch(url)
        if not fetch_result.success:
            error = fetch_result.error or "读取网页失败"
            return AgentResult(
                content=f"这个网址我暂时打不开，{error}。",
                needs_persona_formatting=True,
            )

        prompt = load_prompt("browser_analyze").format(
            user_message=task.user_message,
            title=fetch_result.title,
            url=url,
            page_text=fetch_result.text,
        )

        try:
            summary = await router.complete(
                [{"role": "user", "content": prompt}],
                purpose="tool",
                max_tokens=1024,
                session_key=f"chat:{task.chat_id}",
                origin="agent.browser.summary",
            )
        except Exception as exc:
            logger.warning(f"[browser] 网页总结失败: {exc}")
            return AgentResult(
                content="网页内容已经读取到了，但我在整理时出了点问题，请稍后再试。",
                needs_persona_formatting=True,
            )

        await self._save_discovery(task.chat_id, fetch_result.title, summary, url)
        if self._knowledge_manager is not None:
            self._knowledge_manager.save_note(
                topic=fetch_result.title or url,
                source_url=url,
                content=summary,
            )

        return AgentResult(
            content=summary,
            needs_persona_formatting=True,
            metadata={"url": url, "title": fetch_result.title},
        )

    def _extract_url(self, user_message: str) -> str | None:
        for match in _URL_PATTERN.findall(user_message):
            candidate = match.rstrip(".,);]}>\"'")
            parsed = urlparse(candidate)
            if parsed.scheme in {"http", "https"} and parsed.netloc:
                return candidate
        return None

    async def _save_discovery(self, chat_id: str, title: str, summary: str, url: str) -> None:
        try:
            await self._memory.add_discovery(
                chat_id=chat_id,
                source="browsing",
                title=title or url,
                summary=summary[:500],
                url=url,
            )
        except Exception as exc:
            logger.warning(f"[browser] 存 discovery 失败: {exc}")
