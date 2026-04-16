"""tests/core/test_trust_tagger.py — 信任标记生成测试。"""

from src.core.trust_tagger import TrustTagger


class TestTrustTagger:
    def test_tag_kevin(self):
        result = TrustTagger.tag_kevin("hello", "desktop", "2026-04-15T10:00:00")
        assert '<kevin_message source="desktop"' in result
        assert "hello" in result
        assert "</kevin_message>" in result

    def test_tag_group(self):
        result = TrustTagger.tag_group("hi", "12345", "Alice", "guest")
        assert '<group_message source="qq_group"' in result
        assert 'sender_id="12345"' in result
        assert 'trust="guest"' in result
        assert "hi" in result

    def test_tag_external(self):
        result = TrustTagger.tag_external("page content", "https://example.com")
        assert 'trust="untrusted"' in result
        assert "page content" in result

    def test_tag_agent(self):
        result = TrustTagger.tag_agent("research result", "researcher", "task_001")
        assert 'agent="researcher"' in result
        assert 'task_id="task_001"' in result
        assert "research result" in result
