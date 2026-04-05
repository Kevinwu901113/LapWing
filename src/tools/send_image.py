"""send_image 工具 — 让 Lapwing 能主动发送图片。"""

from __future__ import annotations

import logging
from pathlib import Path

from src.tools.types import ToolExecutionContext, ToolExecutionRequest, ToolExecutionResult

logger = logging.getLogger("lapwing.tools.send_image")


async def _execute_send_image(
    request: ToolExecutionRequest,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    channel_manager = context.services.get("channel_manager")
    if channel_manager is None:
        return ToolExecutionResult(
            success=False,
            payload={"error": "channel_manager 不可用"},
            reason="channel_manager unavailable",
        )

    args = request.arguments
    url = str(args.get("url", "")).strip() or None
    path = str(args.get("path", "")).strip() or None
    caption = str(args.get("caption", "")).strip()

    if not url and not path:
        return ToolExecutionResult(
            success=False,
            payload={"error": "必须提供 url 或 path 参数"},
            reason="missing image source",
        )

    if path:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return ToolExecutionResult(
                success=False,
                payload={"error": f"文件不存在: {path}"},
                reason="file_not_found",
            )
        path = str(p)

    try:
        await channel_manager.send_image_to_owner(url=url, path=path, caption=caption)
        return ToolExecutionResult(
            success=True,
            payload={"sent": True, "url": url or "", "path": path or "", "caption": caption},
        )
    except Exception as exc:
        logger.warning("send_image 失败: %s", exc)
        return ToolExecutionResult(
            success=False,
            payload={"error": f"发送图片失败: {exc}"},
            reason=str(exc),
        )


SEND_IMAGE_EXECUTORS = {
    "send_image": _execute_send_image,
}
