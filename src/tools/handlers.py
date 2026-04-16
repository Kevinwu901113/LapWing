"""Tool execution handlers — extracted from registry.py for clarity."""

from __future__ import annotations

import logging
import shlex
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from config.settings import ROOT_DIR, SEARCH_MAX_RESULTS, WEB_FETCH_MAX_CHARS
from src.core import verifier
from src.tools import code_runner, file_editor, web_fetcher, web_search
from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.handlers")

_WEB_SEARCH_MAX_RESULTS_CAP = 10
_WEB_FETCH_MAX_CHARS_CAP = WEB_FETCH_MAX_CHARS


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _blocked_payload(*, reason: str, cwd: str, command: str = "") -> dict[str, Any]:
    return {
        "command": command,
        "stdout": "",
        "stderr": "",
        "return_code": -1,
        "timed_out": False,
        "blocked": True,
        "reason": reason,
        "cwd": cwd,
        "stdout_truncated": False,
        "stderr_truncated": False,
    }


def _workspace_root(context: ToolExecutionContext) -> Path:
    raw = context.workspace_root.strip() if context.workspace_root else ""
    if not raw:
        return ROOT_DIR.resolve()
    return Path(raw).resolve()


def _file_payload(result: file_editor.FileEditResult) -> dict[str, Any]:
    return {
        "operation": result.operation,
        "path": result.path,
        "success": result.success,
        "changed": result.changed,
        "reason": result.reason,
        "content": result.content,
        "diff": result.diff,
        "backup_path": result.backup_path,
        "metadata": result.metadata,
    }


def _clamp_web_search_max_results(raw: Any) -> int:
    try:
        resolved = int(raw)
    except (TypeError, ValueError):
        resolved = SEARCH_MAX_RESULTS
    return max(1, min(_WEB_SEARCH_MAX_RESULTS_CAP, resolved))


def _clamp_web_fetch_max_chars(raw: Any) -> int:
    try:
        resolved = int(raw)
    except (TypeError, ValueError):
        return _WEB_FETCH_MAX_CHARS_CAP
    return max(1, min(_WEB_FETCH_MAX_CHARS_CAP, resolved))


# ---------------------------------------------------------------------------
# Execution handlers
# ---------------------------------------------------------------------------


async def execute_shell_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    command = str(request.arguments.get("command", "")).strip()
    if not command:
        reason = "工具参数缺少 command。"
        return ToolExecutionResult(
            success=False,
            reason=reason,
            payload=_blocked_payload(reason=reason, cwd=context.shell_default_cwd, command=""),
        )

    result = await context.execute_shell(command)
    payload = {
        "command": command,
        **result.to_dict(),
    }
    return ToolExecutionResult(
        success=(result.return_code == 0 and not result.blocked and not result.timed_out),
        payload=payload,
        reason=result.reason or "",
        shell_result=result,
    )


async def read_file_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    if not path:
        payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 path 参数")

    result = await context.execute_shell(f"cat {shlex.quote(path)}")
    payload = {"path": path, **result.to_dict()}
    return ToolExecutionResult(
        success=(result.return_code == 0 and not result.blocked and not result.timed_out),
        payload=payload,
        reason=result.reason or "",
        shell_result=result,
    )


async def write_file_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    if not path:
        payload = {"error": "缺少 path 参数", "stdout": "", "return_code": -1}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 path 参数")

    from pathlib import Path as _Path
    target = _Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    payload = {
        "path": path,
        "action": "written",
        "bytes_written": len(content.encode("utf-8")),
        "stdout": "",
        "return_code": 0,
    }
    return ToolExecutionResult(
        success=True,
        payload=payload,
        reason="",
    )


from src.core.vitals import _TAIPEI_TZ as _TZ_TAIPEI
_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def _build_search_summarizer_system() -> str:
    now = datetime.now(_TZ_TAIPEI)
    date_str = now.strftime("%Y年%m月%d日")
    weekday = _WEEKDAYS[now.weekday()]
    return (
        f"你是一个信息提取助手。当前日期是 {date_str}（星期{weekday}）。\n\n"
        "规则：\n"
        "1. 只输出与问题直接相关的信息\n"
        f"2. 用户问\u201c今天\u201d就是指 {date_str}，\u201c昨天\u201d就是前一天，以此类推。"
        "如果搜索结果中没有匹配该日期的内容，明确说\"未找到该日期的相关信息\"，不要用其他日期的结果替代\n"
        "3. 保留具体的数据（数字、日期、排名、比分等），不要模糊化\n"
        "4. 标注信息来源的网站名称\n"
        "5. 控制在 500 字以内\n"
        "6. 不要编造搜索结果中没有的信息"
    )


async def _summarize_search_results(
    query: str,
    raw_results: list[dict[str, Any]],
    llm_router: Any,
) -> str:
    """用轻量模型预处理搜索结果，返回精炼答案。"""
    if not raw_results:
        return "没有找到相关搜索结果。"

    parts = []
    for i, r in enumerate(raw_results[:5], 1):
        content = str(r.get("snippet", "") or r.get("content", "") or r.get("body", ""))
        title = str(r.get("title", "无标题"))
        url = str(r.get("url", ""))
        truncated = content[:3000]
        parts.append(f"[结果 {i}]\n标题: {title}\n来源: {url}\n内容: {truncated}")

    combined = "\n\n---\n\n".join(parts)
    user_message = f"用户搜索: {query}\n\n搜索结果:\n{combined}\n\n请提炼出与搜索问题最相关的信息。"

    try:
        response = await llm_router.query_lightweight(
            system=_build_search_summarizer_system(),
            user=user_message,
            slot="lightweight_judgment",
        )
        return response
    except Exception as e:
        logger.warning("搜索结果预处理失败: %s，降级为标题列表", e)
        fallback = []
        for r in raw_results[:5]:
            title = str(r.get("title", ""))
            snippet = str(r.get("snippet", ""))[:200]
            url = str(r.get("url", ""))
            fallback.append(f"- {title}: {snippet}... ({url})")
        return "搜索结果（未预处理）:\n" + "\n".join(fallback)


async def web_search_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    query = str(request.arguments.get("query", "")).strip()
    if not query:
        payload: dict[str, Any] = {"query": "", "count": 0, "results": []}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 query 参数")

    max_results = _clamp_web_search_max_results(request.arguments.get("max_results"))
    try:
        results = await web_search.search(query, max_results=max_results)
    except Exception as exc:
        payload = {"query": query, "count": 0, "results": []}
        return ToolExecutionResult(
            success=False, payload=payload, reason=f"web_search 执行失败: {exc}"
        )

    # 构建轻量结果列表（title + url + 短 snippet）
    light_results: list[dict[str, Any]] = []
    for item in results[:8]:
        entry: dict[str, Any] = {
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "snippet": str(item.get("snippet", ""))[:200],
        }
        if item.get("published_date"):
            entry["published_date"] = item["published_date"]
        if item.get("relevance_score") is not None:
            entry["relevance_score"] = item["relevance_score"]
        light_results.append(entry)

    # 尝试用轻量模型预处理搜索结果
    llm_router = context.services.get("router")
    if llm_router and results:
        summary = await _summarize_search_results(query, results, llm_router)
        payload = {
            "query": query,
            "answer": summary,
            "sources": [{"title": r["title"], "url": r["url"]} for r in light_results],
            "result_count": len(results),
        }
    else:
        # 降级：llm_router 不可用时走轻量列表
        payload = {
            "query": query,
            "count": len(results),
            "results": light_results,
            "note": "以上是搜索结果摘要。如需查看某条结果的完整内容，请使用 web_fetch 工具访问对应 URL。",
        }
        payload["_system_hint"] = (
            "以上是搜索摘要。如果这些摘要不包含回答用户问题所需的具体数据"
            "（如具体排名、比分、日期、数字），请用 web_fetch 抓取相关 URL 获取完整内容后再回答。"
            "不要用你的训练知识填补搜索结果中缺失的具体信息。"
        )
    return ToolExecutionResult(success=True, payload=payload, reason="")


def _build_fetch_extractor_system() -> str:
    now = datetime.now(_TZ_TAIPEI)
    date_str = now.strftime("%Y年%m月%d日")
    return (
        f"你是一个信息提取助手。当前日期是 {date_str}。"
        "根据网页内容回答用户的问题。"
        "只输出与问题直接相关的信息，保留具体数据（数字、日期、排名、比分等）。"
        "如果内容无法回答问题，明确说明。控制在 500 字以内。"
    )


async def web_fetch_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    url = str(request.arguments.get("url", "")).strip()
    if not url:
        payload: dict[str, Any] = {"url": "", "title": "", "text": "", "success": False, "error": "缺少 url 参数"}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 url 参数")

    question = str(request.arguments.get("question", "")).strip()

    try:
        fetched = await web_fetcher.fetch(url)
    except Exception as exc:
        payload = {
            "url": url, "title": "", "text": "", "success": False,
            "error": f"web_fetch 执行失败: {exc}",
        }
        return ToolExecutionResult(success=False, payload=payload, reason=payload["error"])

    if not fetched.success:
        payload = {
            "url": fetched.url, "title": fetched.title, "text": "",
            "success": False, "error": fetched.error,
        }
        return ToolExecutionResult(success=False, payload=payload, reason=fetched.error)

    max_chars = _clamp_web_fetch_max_chars(request.arguments.get("max_chars"))
    text = fetched.text[:max_chars]

    # 有 question 且有 llm_router → 用轻量模型提取答案
    llm_router = context.services.get("router")
    if question and llm_router and text:
        try:
            answer = await llm_router.query_lightweight(
                system=_build_fetch_extractor_system(),
                user=f"用户问题: {question}\n\n网页内容:\n{text[:8000]}",
                slot="lightweight_judgment",
            )
            payload = {
                "url": fetched.url,
                "title": fetched.title,
                "answer": answer,
                "source_url": url,
                "success": True,
            }
            if fetched.published_date:
                payload["published_date"] = fetched.published_date
            return ToolExecutionResult(success=True, payload=payload, reason="")
        except Exception as exc:
            logger.warning("web_fetch LLM 预处理失败: %s，降级返回原文", exc)

    # 无 question 或 LLM 降级：返回截断内容
    payload = {
        "url": fetched.url,
        "title": fetched.title,
        "text": text,
        "success": fetched.success,
        "error": fetched.error,
    }
    if fetched.published_date:
        payload["published_date"] = fetched.published_date
    if fetched.fetched_at:
        payload["fetched_at"] = fetched.fetched_at
    return ToolExecutionResult(success=fetched.success, payload=payload, reason=fetched.error)


async def activate_skill_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    skill_manager = context.services.get("skill_manager")
    if skill_manager is None:
        payload = {"success": False, "reason": "skill_manager 不可用", "skill_name": "", "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason="skill_manager 不可用")

    name = str(request.arguments.get("name", "")).strip().lower()
    user_input = str(request.arguments.get("user_input", "")).strip()
    if not name:
        payload = {"success": False, "reason": "缺少 name 参数", "skill_name": "", "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 name 参数")

    try:
        activated = skill_manager.activate(name, user_input=user_input)
    except KeyError:
        payload = {"success": False, "reason": f"技能不存在: {name}", "skill_name": name, "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason=f"技能不存在: {name}")
    except Exception as exc:
        payload = {"success": False, "reason": f"激活技能失败: {exc}", "skill_name": name, "content": "", "resources": [], "metadata": {}}
        return ToolExecutionResult(success=False, payload=payload, reason=f"激活技能失败: {exc}")

    payload = {
        "success": True,
        "reason": "",
        "skill_name": activated.get("skill_name", name),
        "skill_dir": activated.get("skill_dir", ""),
        "content": activated.get("content", ""),
        "resources": activated.get("resources", []),
        "metadata": activated.get("metadata", {}),
        "wrapped_content": activated.get("wrapped_content", ""),
    }
    return ToolExecutionResult(success=True, payload=payload, reason="")


async def file_read_segment_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    start_line = int(request.arguments.get("start_line", 1) or 1)
    end_line = int(request.arguments.get("end_line", 10**9) or 10**9)
    result = file_editor.read_file_segment(
        path,
        start_line=start_line,
        end_line=end_line,
        root_dir=_workspace_root(context),
    )
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def file_write_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    result = file_editor.write_file(path, content=content, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def file_append_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip()
    content = str(request.arguments.get("content", ""))
    result = file_editor.append_to_file(path, content=content, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def file_list_directory_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    path = str(request.arguments.get("path", "")).strip() or "."
    result = file_editor.list_directory(path, root_dir=_workspace_root(context))
    payload = _file_payload(result)
    return ToolExecutionResult(success=result.success, payload=payload, reason=result.reason)


async def apply_workspace_patch_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    operations = request.arguments.get("operations")
    if not isinstance(operations, list) or not operations:
        payload = {"success": False, "reason": "缺少 operations 参数", "changed_files": []}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 operations 参数")

    tx = file_editor.transactional_apply(operations, root_dir=_workspace_root(context))
    payload = {
        "success": tx.success,
        "reason": tx.reason,
        "changed_files": tx.changed_files,
        "rolled_back": tx.rolled_back,
        "results": [
            {
                "operation": item.operation,
                "path": item.path,
                "success": item.success,
                "changed": item.changed,
                "reason": item.reason,
                "metadata": item.metadata,
            }
            for item in tx.results
        ],
    }
    return ToolExecutionResult(success=tx.success, payload=payload, reason=tx.reason)


async def run_python_code_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    code = str(request.arguments.get("code", ""))
    timeout = int(request.arguments.get("timeout", 10) or 10)
    result = await code_runner.run_python(code, timeout=timeout)
    payload = {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
    }
    success = result.exit_code == 0 and not result.timed_out
    return ToolExecutionResult(success=success, payload=payload, reason=result.stderr.strip())


async def verify_code_result_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    del context
    exit_code_raw = request.arguments.get("exit_code", -1)
    try:
        exit_code = int(exit_code_raw)
    except (TypeError, ValueError):
        exit_code = -1
    result = code_runner.CodeResult(
        stdout=str(request.arguments.get("stdout", "")),
        stderr=str(request.arguments.get("stderr", "")),
        exit_code=exit_code,
        timed_out=bool(request.arguments.get("timed_out", False)),
    )
    require_stdout = bool(request.arguments.get("require_stdout", False))
    verified = verifier.verify_code_result(result, require_stdout=require_stdout)
    payload = {
        "passed": verified.passed,
        "status": verified.status,
        "reason": verified.reason,
        "checks": verified.checks,
        "artifacts": verified.artifacts,
    }
    return ToolExecutionResult(success=verified.passed, payload=payload, reason=verified.reason)


async def verify_workspace_tool(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    changed_files = request.arguments.get("changed_files")
    if not isinstance(changed_files, list):
        payload = {"passed": False, "status": "failed", "reason": "缺少 changed_files 参数"}
        return ToolExecutionResult(success=False, payload=payload, reason="缺少 changed_files 参数")

    pytest_targets_raw = request.arguments.get("pytest_targets")
    pytest_targets = (
        [str(item) for item in pytest_targets_raw if str(item).strip()]
        if isinstance(pytest_targets_raw, list)
        else None
    )
    verified = await verifier.verify_workspace(
        changed_files=[str(item) for item in changed_files],
        root_dir=_workspace_root(context),
        pytest_targets=pytest_targets,
    )
    payload = {
        "passed": verified.passed,
        "status": verified.status,
        "reason": verified.reason,
        "checks": verified.checks,
        "artifacts": verified.artifacts,
    }
    return ToolExecutionResult(success=verified.passed, payload=payload, reason=verified.reason)


