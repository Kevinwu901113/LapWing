"""Tests for ConstitutionGuard."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestHardConstraints:
    def test_passes_valid_text(self):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        violations = guard.validate_hard_constraints("Lapwing 有白发蓝眸，她喜欢安静。")
        assert violations == []

    def test_detects_missing_lapwing(self):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        violations = guard.validate_hard_constraints("白发蓝眸，她喜欢安静。")
        assert any("Lapwing" in v for v in violations)

    def test_detects_missing_bai_fa(self):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        violations = guard.validate_hard_constraints("Lapwing 喜欢安静。")
        assert any("白发" in v for v in violations)

    def test_detects_forbidden_ai_chinese(self):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        violations = guard.validate_hard_constraints("Lapwing 白发，我是AI，很好用。")
        assert any("我是AI" in v for v in violations)

    def test_detects_forbidden_ai_english(self):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        violations = guard.validate_hard_constraints("Lapwing 白发. I am an AI assistant.")
        assert any("I am an AI" in v for v in violations)

    def test_detects_multiple_violations(self):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        # Missing both markers + has forbidden ref
        violations = guard.validate_hard_constraints("我是AI，很有用。")
        assert len(violations) >= 3  # missing Lapwing, missing 白发, forbidden ref


class TestValidateEvolution:
    async def test_approves_valid_change(self):
        from src.core.constitution_guard import ConstitutionGuard

        router = MagicMock()
        router.complete_structured = AsyncMock(return_value={"approved": True, "violations": []})
        guard = ConstitutionGuard(router)

        with patch.object(type(guard), "constitution", new_callable=lambda: property(lambda self: "宪法内容")):
            result = await guard.validate_evolution(
                "Lapwing 白发蓝眸。",
                [{"action": "add", "description": "追加了对音乐的喜爱"}],
            )

        assert result["approved"] is True
        assert result["violations"] == []

    async def test_rejects_violating_change(self):
        from src.core.constitution_guard import ConstitutionGuard

        router = MagicMock()
        router.complete_structured = AsyncMock(
            return_value={"approved": False, "violations": ["违反了身份规则"]}
        )
        guard = ConstitutionGuard(router)

        with patch.object(type(guard), "constitution", new_callable=lambda: property(lambda self: "宪法内容")):
            result = await guard.validate_evolution(
                "Lapwing 白发蓝眸。",
                [{"action": "remove", "description": "删除了身份描述"}],
            )

        assert result["approved"] is False
        assert "违反了身份规则" in result["violations"]

    async def test_handles_llm_failure(self):
        from src.core.constitution_guard import ConstitutionGuard

        router = MagicMock()
        router.complete_structured = AsyncMock(side_effect=RuntimeError("network error"))
        guard = ConstitutionGuard(router)

        with patch.object(type(guard), "constitution", new_callable=lambda: property(lambda self: "宪法内容")):
            result = await guard.validate_evolution(
                "Lapwing 白发蓝眸。",
                [{"action": "add", "description": "测试"}],
            )

        assert result["approved"] is False
        assert len(result["violations"]) > 0


class TestReload:
    def test_reload_clears_constitution_cache(self, tmp_path):
        from src.core.constitution_guard import ConstitutionGuard
        guard = ConstitutionGuard(MagicMock())
        guard._constitution = "old constitution"
        guard.reload()
        assert guard._constitution is None
