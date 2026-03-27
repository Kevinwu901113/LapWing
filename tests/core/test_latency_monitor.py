"""LatencyMonitor 单元测试。"""

from __future__ import annotations

import logging
import time

from src.core.latency_monitor import LatencyMonitor


def test_shell_long_command_is_excluded_from_slo_window():
    monitor = LatencyMonitor(
        window_size=20,
        min_samples_for_slo=1,
        shell_p95_threshold_ms=2000,
        web_p95_threshold_ms=5000,
        long_command_cutoff_ms=10000,
    )
    monitor.record_tool_loop_round(bucket="shell_local", duration_ms=1200)
    monitor.record_tool_loop_round(bucket="shell_local", duration_ms=12001)

    snapshot = monitor.snapshot()
    shell_metric = snapshot["backend"]["tool_loop"]["shell_local"]
    assert shell_metric["samples"] == 1
    assert shell_metric["p95_ms"] == 1200
    assert shell_metric["long_command_excluded"] == 1


def test_slo_exceeded_logs_warning_without_interrupting_flow(caplog):
    monitor = LatencyMonitor(
        window_size=20,
        min_samples_for_slo=2,
        shell_p95_threshold_ms=1000,
        web_p95_threshold_ms=5000,
        long_command_cutoff_ms=10000,
    )
    with caplog.at_level(logging.WARNING):
        monitor.record_tool_loop_round(bucket="shell_local", duration_ms=1200)
        monitor.record_tool_loop_round(bucket="shell_local", duration_ms=1300)

    assert "SLO exceeded" in caplog.text
    shell_metric = monitor.snapshot()["backend"]["tool_loop"]["shell_local"]
    assert shell_metric["samples"] == 2
    assert shell_metric["slo_exceeded"] is True


def test_event_publish_to_sse_latency_is_recorded():
    monitor = LatencyMonitor(window_size=20, min_samples_for_slo=1)
    event = {
        "type": "task.started",
        "timestamp": "2026-03-27T00:00:00+00:00",
        "payload": {"task_id": "task_1"},
    }
    monitor.record_event_published(event)
    time.sleep(0.005)
    monitor.record_event_stream_emitted(event)

    metric = monitor.snapshot()["backend"]["event_pipeline"]["publish_to_sse"]
    assert metric["samples"] == 1
    assert metric["p95_ms"] is not None
    assert metric["p95_ms"] >= 0


def test_frontend_latency_samples_are_aggregated():
    monitor = LatencyMonitor(window_size=20, min_samples_for_slo=1)
    accepted = monitor.record_frontend_start_to_ui_samples([100, 200, -1, float("nan")])
    assert accepted == 2

    metric = monitor.snapshot()["frontend"]["tool_execution_start_to_ui"]
    assert metric["samples"] == 2
    assert metric["p95_ms"] == 200
