"""reasoning tag 清洗测试。"""

from src.core.reasoning_tags import strip_internal_thinking_tags


def test_strips_thinking_tag_variants():
    text = "A <think>1</think> B <thinking>2</thinking> C <thought>3</thought> D <antthinking>4</antthinking> E"
    result = strip_internal_thinking_tags(text)
    assert result == "A  B  C  D  E"


def test_strips_case_insensitive_and_attributes():
    text = "Hello <THINK id='x'>secret</ThInK> world"
    result = strip_internal_thinking_tags(text)
    assert result == "Hello  world"


def test_preserves_tags_inside_code_regions():
    text = (
        "Use `<think>example</think>` literally.\n\n"
        "```xml\n"
        "<think>inside code</think>\n"
        "```\n\n"
        "<think>hidden</think>Visible"
    )
    result = strip_internal_thinking_tags(text)
    assert "`<think>example</think>`" in result
    assert "<think>inside code</think>" in result
    assert "hidden" not in result
    assert result.endswith("Visible")


def test_drops_tail_after_unclosed_thinking_tag():
    text = "Before <think>secret still hidden"
    result = strip_internal_thinking_tags(text)
    assert result == "Before "
