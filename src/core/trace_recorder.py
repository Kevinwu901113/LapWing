"""执行轨迹记录器 — 记录每次任务执行的详情，供 Skill 孵化使用。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("lapwing.core.trace_recorder")

_DATE_FORMAT = "%Y-%m-%d"
_ARCHIVE_THRESHOLD = 500


@dataclass
class SkillUsageInfo:
    id: str
    version: int
    match_level: str  # "quick" | "index"
    deviated: bool = False
    deviation_notes: str | None = None


@dataclass
class ExecutionDetails:
    total_duration_seconds: float
    agents_called: list[dict[str, Any]] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    llm_calls: int = 0
    tokens_used: int = 0


@dataclass
class UserFeedback:
    type: str  # "positive" | "negative" | "neutral"
    details: str = ""
    timestamp: str = ""


@dataclass
class ExecutionTrace:
    trace_id: str
    timestamp: str
    user_request: str
    request_category: str
    intent_summary: str
    execution: ExecutionDetails
    output_summary: str
    skill_used: SkillUsageInfo | None = None
    user_feedback: UserFeedback | None = None


class TraceRecorder:
    """将执行轨迹写入 skill_traces/ 目录。"""

    def __init__(self, traces_dir: Path) -> None:
        self._traces_dir = traces_dir

    def ensure_dir(self) -> None:
        self._traces_dir.mkdir(parents=True, exist_ok=True)

    def record_trace(self, trace: ExecutionTrace) -> Path:
        """将轨迹写入 JSON 文件，返回文件路径。"""
        self.ensure_dir()
        self._maybe_archive()

        trace_path = self._traces_dir / f"{trace.trace_id}.json"
        try:
            trace_path.write_text(
                json.dumps(_serialize_trace(trace), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("轨迹已记录: %s", trace.trace_id)
        except Exception as exc:
            logger.warning("记录轨迹失败: %s", exc)

        return trace_path

    def mark_trace(self, reason: str, category: str = "general") -> str:
        """在 _marks/ 子目录写入一条标记，返回 mark_id。

        标记供自省时优先处理——不立即创建 Skill，只是提示今晚值得回顾。
        """
        import json as _json
        marks_dir = self._traces_dir / "_marks"
        marks_dir.mkdir(parents=True, exist_ok=True)

        today = date.today().isoformat()
        existing = list(marks_dir.glob(f"{today}_{category}_*.json"))
        seq = len(existing) + 1
        mark_id = f"{today}_{category}_{seq:03d}"

        mark_data = {
            "mark_id": mark_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "reason": reason,
            "reviewed": False,
        }

        mark_path = marks_dir / f"{mark_id}.json"
        try:
            mark_path.write_text(
                _json.dumps(mark_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.debug("轨迹标记已写入: %s — %s", mark_id, reason)
        except Exception as exc:
            logger.warning("写入标记失败: %s", exc)

        return mark_id

    def get_marked_traces(self, days: int = 7) -> list[dict[str, Any]]:
        """读取最近 N 天未审阅的标记记录，供自省时优先处理。"""
        marks_dir = self._traces_dir / "_marks"
        if not marks_dir.exists():
            return []

        cutoff = _days_ago_str(days)
        results: list[dict[str, Any]] = []

        for mark_file in sorted(marks_dir.glob("*.json")):
            parts = mark_file.stem.split("_")
            if parts and parts[0] >= cutoff:
                try:
                    data = json.loads(mark_file.read_text(encoding="utf-8"))
                    if not data.get("reviewed", False):
                        results.append(data)
                except Exception as exc:
                    logger.warning("读取标记文件 %s 失败: %s", mark_file, exc)

        return results

    def mark_as_reviewed(self, mark_id: str) -> None:
        """将指定标记置为已审阅。"""
        marks_dir = self._traces_dir / "_marks"
        mark_path = marks_dir / f"{mark_id}.json"
        if not mark_path.exists():
            return
        try:
            data = json.loads(mark_path.read_text(encoding="utf-8"))
            data["reviewed"] = True
            mark_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("标记 %s 置为已审阅失败: %s", mark_id, exc)

    def get_recent_traces(self, days: int = 7) -> list[dict[str, Any]]:
        """读取最近 N 天的轨迹列表（供 Phase 2 孵化使用）。"""
        if not self._traces_dir.exists():
            return []

        cutoff = _days_ago_str(days)
        results: list[dict[str, Any]] = []

        for trace_file in sorted(self._traces_dir.glob("*.json")):
            # 文件名格式：{date}_{category}_{seq}.json
            parts = trace_file.stem.split("_")
            if parts and parts[0] >= cutoff:
                try:
                    data = json.loads(trace_file.read_text(encoding="utf-8"))
                    results.append(data)
                except Exception as exc:
                    logger.warning("读取轨迹文件 %s 失败: %s", trace_file, exc)

        return results

    def generate_trace_id(self, category: str = "general") -> str:
        """生成轨迹 ID：{date}_{category}_{seq:03d}。"""
        today = date.today().strftime(_DATE_FORMAT)
        prefix = f"{today}_{category}_"
        existing = list(self._traces_dir.glob(f"{prefix}*.json")) if self._traces_dir.exists() else []
        sequence = len(existing) + 1
        return f"{prefix}{sequence:03d}"

    def build_trace(
        self,
        *,
        user_request: str,
        output_summary: str,
        duration_seconds: float,
        skill_used: SkillUsageInfo | None = None,
        category: str = "general",
    ) -> ExecutionTrace:
        """构造一个 Phase 1 简化轨迹对象。"""
        trace_id = self.generate_trace_id(category)
        timestamp = datetime.now(timezone.utc).isoformat()
        intent_summary = user_request[:100].strip()

        return ExecutionTrace(
            trace_id=trace_id,
            timestamp=timestamp,
            user_request=user_request,
            request_category=category,
            intent_summary=intent_summary,
            execution=ExecutionDetails(
                total_duration_seconds=round(duration_seconds, 2),
            ),
            output_summary=output_summary[:200],
            skill_used=skill_used,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _maybe_archive(self) -> None:
        """当 skill_traces/ 文件超过阈值时，将旧文件移入 archive/ 子目录。"""
        try:
            files = list(self._traces_dir.glob("*.json"))
            if len(files) <= _ARCHIVE_THRESHOLD:
                return

            archive_dir = self._traces_dir / "archive"
            archive_dir.mkdir(exist_ok=True)

            # 按文件名排序，保留最新 300 个，其余归档
            files.sort(key=lambda p: p.name)
            to_archive = files[: len(files) - 300]
            for f in to_archive:
                f.rename(archive_dir / f.name)
            logger.info("已归档 %d 条旧轨迹", len(to_archive))
        except Exception as exc:
            logger.warning("轨迹归档失败: %s", exc)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _serialize_trace(trace: ExecutionTrace) -> dict[str, Any]:
    data = asdict(trace)
    # asdict 会把 dataclass 转为 dict，None 字段保留为 None
    return data


def _days_ago_str(days: int) -> str:
    from datetime import timedelta
    cutoff_date = date.today() - timedelta(days=days)
    return cutoff_date.strftime(_DATE_FORMAT)
