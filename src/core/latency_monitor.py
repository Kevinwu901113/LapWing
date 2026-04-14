"""Latency SLO 监控：滚动窗口、P95 统计与告警。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import time
from typing import Any

from config.settings import (
    TOOL_EVENT_START_TO_UI_P95_MS,
    TOOL_EVENT_UPDATE_THROTTLE_MS,
    TOOL_LATENCY_MIN_SAMPLES_FOR_SLO,
    TOOL_LATENCY_WINDOW_SIZE,
    TOOL_LOOP_LONG_COMMAND_CUTOFF_MS,
    TOOL_LOOP_SLO_SHELL_P95_MS,
    TOOL_LOOP_SLO_WEB_P95_MS,
)

from src.core.time_utils import now_iso

logger = logging.getLogger("lapwing.core.latency_monitor")


@dataclass
class _RollingMetric:
    threshold_ms: int
    min_samples_for_slo: int
    samples: deque[int]
    last_updated: str | None = None

    def add_sample(self, value_ms: int) -> None:
        self.samples.append(max(int(value_ms), 0))
        self.last_updated = now_iso()

    def p95(self) -> int | None:
        if not self.samples:
            return None
        sorted_values = sorted(self.samples)
        index = max(math.ceil(len(sorted_values) * 0.95) - 1, 0)
        return int(sorted_values[index])

    def enough_samples(self) -> bool:
        return len(self.samples) >= self.min_samples_for_slo

    def snapshot(self) -> dict[str, Any]:
        p95_ms = self.p95()
        enough = self.enough_samples()
        no_data = p95_ms is None
        exceeded = (p95_ms is not None and enough and p95_ms > self.threshold_ms)
        return {
            "p95_ms": p95_ms,
            "samples": len(self.samples),
            "threshold_ms": self.threshold_ms,
            "slo_exceeded": bool(exceeded),
            "no_data": no_data,
            "enough_samples": enough,
            "last_updated": self.last_updated,
        }


class LatencyMonitor:
    """统一收集 backend/frontend 体感延迟指标。"""

    def __init__(
        self,
        *,
        window_size: int = TOOL_LATENCY_WINDOW_SIZE,
        min_samples_for_slo: int = TOOL_LATENCY_MIN_SAMPLES_FOR_SLO,
        shell_p95_threshold_ms: int = TOOL_LOOP_SLO_SHELL_P95_MS,
        web_p95_threshold_ms: int = TOOL_LOOP_SLO_WEB_P95_MS,
        long_command_cutoff_ms: int = TOOL_LOOP_LONG_COMMAND_CUTOFF_MS,
        start_to_ui_threshold_ms: int = TOOL_EVENT_START_TO_UI_P95_MS,
        update_throttle_ms: int = TOOL_EVENT_UPDATE_THROTTLE_MS,
    ) -> None:
        self._window_size = max(int(window_size), 1)
        self._min_samples_for_slo = max(int(min_samples_for_slo), 1)
        self._long_command_cutoff_ms = max(int(long_command_cutoff_ms), 1)
        self._update_throttle_ms = max(int(update_throttle_ms), 1)

        self._tool_loop_shell = _RollingMetric(
            threshold_ms=max(int(shell_p95_threshold_ms), 1),
            min_samples_for_slo=self._min_samples_for_slo,
            samples=deque(maxlen=self._window_size),
        )
        self._tool_loop_web = _RollingMetric(
            threshold_ms=max(int(web_p95_threshold_ms), 1),
            min_samples_for_slo=self._min_samples_for_slo,
            samples=deque(maxlen=self._window_size),
        )
        self._event_publish_to_sse = _RollingMetric(
            threshold_ms=max(int(start_to_ui_threshold_ms), 1),
            min_samples_for_slo=self._min_samples_for_slo,
            samples=deque(maxlen=self._window_size),
        )
        self._frontend_start_to_ui = _RollingMetric(
            threshold_ms=max(int(start_to_ui_threshold_ms), 1),
            min_samples_for_slo=self._min_samples_for_slo,
            samples=deque(maxlen=self._window_size),
        )

        self._shell_long_command_excluded = 0
        self._last_updated = now_iso()
        self._warn_state: dict[str, bool] = {}

        self._pending_event_published_at: dict[int, float] = {}
        self._pending_event_limit = max(self._window_size * 20, 100)

    def record_tool_loop_round(self, *, bucket: str, duration_ms: int) -> None:
        value_ms = max(int(duration_ms), 0)
        if bucket == "shell_local":
            if value_ms > self._long_command_cutoff_ms:
                self._shell_long_command_excluded += 1
                self._last_updated = now_iso()
                return
            metric = self._tool_loop_shell
            warn_key = "tool_loop.shell_local"
        else:
            metric = self._tool_loop_web
            warn_key = "tool_loop.web_search"

        metric.add_sample(value_ms)
        self._last_updated = metric.last_updated or now_iso()
        self._warn_if_needed(warn_key, metric)

    def record_event_published(self, event: dict[str, Any]) -> None:
        event_id = id(event)
        self._pending_event_published_at[event_id] = time.perf_counter()
        if len(self._pending_event_published_at) <= self._pending_event_limit:
            return

        overflow = len(self._pending_event_published_at) - self._pending_event_limit
        for key in list(self._pending_event_published_at.keys())[:overflow]:
            self._pending_event_published_at.pop(key, None)

    def record_event_stream_emitted(self, event: dict[str, Any]) -> None:
        event_id = id(event)
        published_at = self._pending_event_published_at.pop(event_id, None)
        if published_at is None:
            return
        duration_ms = max(int((time.perf_counter() - published_at) * 1000), 0)
        self._event_publish_to_sse.add_sample(duration_ms)
        self._last_updated = self._event_publish_to_sse.last_updated or now_iso()
        self._warn_if_needed("event_pipeline.publish_to_sse", self._event_publish_to_sse)

    def record_frontend_start_to_ui_samples(self, samples_ms: list[float]) -> int:
        accepted = 0
        for value in samples_ms:
            if not isinstance(value, (int, float)):
                continue
            if math.isnan(value) or math.isinf(value):
                continue
            if value < 0:
                continue
            self._frontend_start_to_ui.add_sample(int(value))
            accepted += 1

        if accepted > 0:
            self._last_updated = self._frontend_start_to_ui.last_updated or now_iso()
            self._warn_if_needed("frontend.start_to_ui", self._frontend_start_to_ui)
        return accepted

    def snapshot(self) -> dict[str, Any]:
        return {
            "backend": {
                "tool_loop": {
                    "shell_local": {
                        **self._tool_loop_shell.snapshot(),
                        "long_command_cutoff_ms": self._long_command_cutoff_ms,
                        "long_command_excluded": self._shell_long_command_excluded,
                    },
                    "web_search": self._tool_loop_web.snapshot(),
                },
                "event_pipeline": {
                    "publish_to_sse": self._event_publish_to_sse.snapshot(),
                    "update_throttle_ms": self._update_throttle_ms,
                },
            },
            "frontend": {
                "tool_execution_start_to_ui": self._frontend_start_to_ui.snapshot(),
            },
            "last_updated": self._last_updated,
        }

    def _warn_if_needed(self, key: str, metric: _RollingMetric) -> None:
        p95_ms = metric.p95()
        exceeded = bool(
            p95_ms is not None
            and metric.enough_samples()
            and p95_ms > metric.threshold_ms
        )
        previous = self._warn_state.get(key, False)
        if exceeded and not previous:
            logger.warning(
                (
                    "[latency] SLO exceeded: metric=%s p95_ms=%s "
                    "samples=%s threshold_ms=%s"
                ),
                key,
                p95_ms,
                len(metric.samples),
                metric.threshold_ms,
            )
        self._warn_state[key] = exceeded
