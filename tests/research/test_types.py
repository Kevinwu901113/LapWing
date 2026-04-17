"""types.py 单元测试。"""

from src.research.types import Evidence, ResearchResult


def test_evidence_to_dict():
    ev = Evidence(source_url="https://example.com", source_name="Example", quote="quote text")
    assert ev.to_dict() == {
        "source_url": "https://example.com",
        "source_name": "Example",
        "quote": "quote text",
    }


def test_research_result_defaults():
    result = ResearchResult(answer="hello")
    assert result.answer == "hello"
    assert result.evidence == []
    assert result.confidence == "medium"
    assert result.unclear == ""
    assert result.search_backend_used == []


def test_research_result_with_evidence():
    ev1 = Evidence(source_url="https://a.com", source_name="A", quote="q1")
    ev2 = Evidence(source_url="https://b.com", source_name="B", quote="q2")
    result = ResearchResult(
        answer="综合答案",
        evidence=[ev1, ev2],
        confidence="high",
        unclear="某处不确定",
        search_backend_used=["tavily", "bocha"],
    )
    assert len(result.evidence) == 2
    assert result.evidence[0].source_url == "https://a.com"
    assert result.confidence == "high"
    assert result.unclear == "某处不确定"
    assert result.search_backend_used == ["tavily", "bocha"]


def test_evidence_independence_between_instances():
    """field(default_factory) 不应在实例间共享状态。"""
    r1 = ResearchResult(answer="a")
    r2 = ResearchResult(answer="b")
    r1.evidence.append(Evidence("u", "n", "q"))
    r1.search_backend_used.append("tavily")
    assert r2.evidence == []
    assert r2.search_backend_used == []
