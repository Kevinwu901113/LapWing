"""tests/core/test_quality_checker.py — ReplyQualityChecker 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.quality_checker import ReplyQualityChecker


def _make_checker(router_response: str, tmp_path: Path) -> ReplyQualityChecker:
    """创建一个使用 mock router 的 checker，samples 目录指向 tmp_path。"""
    import src.core.quality_checker as qc_module
    # 重定向 DIAGNOSTICS_SAMPLES_DIR 到 tmp_path
    import importlib
    import config.settings as settings_mod

    router = MagicMock()
    router.query_lightweight = AsyncMock(return_value=router_response)
    checker = ReplyQualityChecker(router)
    # Patch DIAGNOSTICS_SAMPLES_DIR inside the module
    qc_module.DIAGNOSTICS_SAMPLES_DIR = tmp_path
    return checker


def _context(n: int = 4) -> list[dict]:
    messages = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"消息 {i}"})
    return messages


# ── 正常回复不触发 ─────────────────────────────────────────────────────────────

async def test_check_returns_none_for_good_reply(tmp_path):
    checker = _make_checker('{"flag": false}', tmp_path)
    result = await checker.check(_context(), "一切正常的回复")
    assert result is None


# ── 问题回复被标记 ─────────────────────────────────────────────────────────────

async def test_check_flags_robotic_reply(tmp_path):
    checker = _make_checker(
        '{"flag": true, "reason": "太机器人了", "scores": {"persona_consistency": 2}}',
        tmp_path,
    )
    result = await checker.check(_context(), "您好，我是您的 AI 助理，请问有什么需要帮助的吗？")
    assert result is not None
    assert result["flag"] is True
    assert "机器人" in result["reason"]
    # 样本文件应被写入
    samples = list(tmp_path.glob("*_auto.md"))
    assert len(samples) == 1


# ── 解析错误不崩溃 ─────────────────────────────────────────────────────────────

async def test_check_handles_parse_error(tmp_path):
    checker = _make_checker("这不是 JSON", tmp_path)
    result = await checker.check(_context(), "正常回复")
    assert result is None  # 解析失败返回 None，不抛出


# ── 上下文过短跳过 ─────────────────────────────────────────────────────────────

async def test_short_context_skipped(tmp_path):
    checker = _make_checker('{"flag": true}', tmp_path)
    result = await checker.check([{"role": "user", "content": "只有一条"}], "回复")
    assert result is None
    # query_lightweight 不应被调用
    checker._router.query_lightweight.assert_not_called()


# ── 样本文件内容 ──────────────────────────────────────────────────────────────

async def test_save_sample_creates_file(tmp_path):
    checker = _make_checker(
        '{"flag": true, "reason": "语气不对"}',
        tmp_path,
    )
    await checker.check(_context(6), "我是助手，有什么可以帮到您？")

    files = list(tmp_path.glob("*_auto.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "语气不对" in content
    assert "Auto-flagged" in content
