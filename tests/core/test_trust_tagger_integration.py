"""tests/core/test_trust_tagger_integration.py — TrustTagger 集成测试。"""

import pytest
from src.core.trust_tagger import TrustTagger


class TestKevinTagging:
    def test_tag_kevin_contains_source(self):
        tagged = TrustTagger.tag_kevin("你好", source="desktop", timestamp="2026-04-15T12:00:00")
        assert '<kevin_message source="desktop"' in tagged
        assert "你好" in tagged

    def test_tag_kevin_qq(self):
        tagged = TrustTagger.tag_kevin("消息", source="qq", timestamp="2026-04-15")
        assert 'source="qq"' in tagged


class TestGroupTagging:
    def test_tag_group_guest(self):
        tagged = TrustTagger.tag_group(
            "群消息",
            sender_id="12345",
            sender_name="小明",
            trust="guest",
        )
        assert '<group_message' in tagged
        assert 'trust="guest"' in tagged
        assert 'sender_id="12345"' in tagged
        assert "群消息" in tagged

    def test_tag_group_trusted(self):
        tagged = TrustTagger.tag_group(
            "朋友消息",
            sender_id="67890",
            sender_name="老王",
            trust="trusted",
        )
        assert 'trust="trusted"' in tagged


class TestExternalTagging:
    def test_tag_external(self):
        tagged = TrustTagger.tag_external(
            "网页内容",
            source_url="https://example.com",
        )
        assert '<external_content' in tagged
        assert 'trust="untrusted"' in tagged
        assert "网页内容" in tagged


class TestAgentTagging:
    def test_tag_agent(self):
        tagged = TrustTagger.tag_agent(
            "Agent 结果",
            agent="researcher",
            task_id="task_001",
        )
        assert '<agent_result' in tagged
        assert 'trust="agent"' in tagged
        assert "Agent 结果" in tagged


class TestDifferentAuthLevels:
    """测试不同权限级别对应的 trust 值。"""

    def test_owner_uses_kevin_tag(self):
        # auth_level == 3 → OWNER → tag_kevin
        tagged = TrustTagger.tag_kevin("指令", source="qq", timestamp="now")
        assert "kevin_message" in tagged

    def test_trusted_uses_trusted(self):
        # auth_level == 2 → TRUSTED → tag_group(trust="trusted")
        tagged = TrustTagger.tag_group("消息", "123", "朋友", trust="trusted")
        assert 'trust="trusted"' in tagged

    def test_guest_uses_guest(self):
        # auth_level <= 1 → GUEST → tag_group(trust="guest")
        tagged = TrustTagger.tag_group("消息", "456", "路人", trust="guest")
        assert 'trust="guest"' in tagged
