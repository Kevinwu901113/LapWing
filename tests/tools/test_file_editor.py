"""file_editor 单元测试。"""

from pathlib import Path

from src.tools import file_editor
from src.tools.file_editor import (
    _match_line_trimmed,
    _match_whitespace_normalized,
    _match_indentation_flexible,
    _fuzzy_find_and_replace,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_replace_in_file_success(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "hello world")

    result = file_editor.replace_in_file(
        str(target),
        old_text="world",
        new_text="lapwing",
        root_dir=tmp_path,
    )

    assert result.success is True
    assert result.changed is True
    assert "lapwing" in target.read_text(encoding="utf-8")


def test_replace_in_file_failure_when_not_found(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "hello world")

    result = file_editor.replace_in_file(
        str(target),
        old_text="missing",
        new_text="lapwing",
        root_dir=tmp_path,
    )

    assert result.success is False
    assert "未找到" in result.reason


def test_replace_in_file_with_regex(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "v1 v2 v3")

    result = file_editor.replace_in_file(
        str(target),
        old_text=r"v\d",
        new_text="X",
        use_regex=True,
        count=2,
        root_dir=tmp_path,
    )

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "X X v3"


def test_replace_lines(tmp_path):
    target = tmp_path / "a.py"
    _write(target, "line1\nline2\nline3\n")

    result = file_editor.replace_lines(
        str(target),
        start_line=2,
        end_line=3,
        new_text="lineX\nlineY",
        root_dir=tmp_path,
    )

    assert result.success is True
    assert target.read_text(encoding="utf-8") == "line1\nlineX\nlineY\n"


def test_batch_apply_multi_files(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    _write(a, "A")
    _write(b, "B")

    results = file_editor.batch_apply(
        [
            {"op": "append_to_file", "path": str(a), "content": "1"},
            {"op": "append_to_file", "path": str(b), "content": "2"},
        ],
        root_dir=tmp_path,
    )

    assert len(results) == 2
    assert all(item.success for item in results)
    assert a.read_text(encoding="utf-8") == "A1"
    assert b.read_text(encoding="utf-8") == "B2"


def test_transactional_apply_rolls_back_on_failure(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "origin")

    result = file_editor.transactional_apply(
        [
            {"op": "append_to_file", "path": str(target), "content": "_new"},
            {"op": "replace_in_file", "path": str(target), "old_text": "missing", "new_text": "x"},
        ],
        root_dir=tmp_path,
    )

    assert result.success is False
    assert result.rolled_back is True
    assert target.read_text(encoding="utf-8") == "origin"


def test_preview_patch_outputs_diff(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "before\n")

    result = file_editor.preview_patch(
        str(target),
        new_content="after\n",
        root_dir=tmp_path,
    )

    assert result.success is True
    assert result.changed is True
    assert "---" in result.diff
    assert "+++" in result.diff


# ── 模糊匹配策略测试 ────────────────────────────────────────────────────────


class TestLineTrimmed:
    def test_trailing_spaces_match(self):
        content = "    hello world   \n    foo bar   \n"
        old_text = "hello world\nfoo bar"
        result = _match_line_trimmed(content, old_text, "replaced", 1)
        assert result is not None
        new_content, matched = result
        assert matched == 1
        assert "replaced" in new_content

    def test_preserves_relative_indentation(self):
        content = "    def foo():\n        pass\n"
        old_text = "def foo():\n    pass"
        new_text = "def bar():\n    return 2"
        result = _match_line_trimmed(content, old_text, new_text, 1)
        assert result is not None
        new_content, matched = result
        assert matched == 1
        # First line should have 4-space indent, second line should have 8-space indent
        lines = new_content.splitlines()
        assert lines[0] == "    def bar():"
        assert lines[1] == "        return 2"

    def test_no_match_different_content(self):
        content = "hello world\n"
        result = _match_line_trimmed(content, "completely different", "x", 1)
        assert result is None


class TestWhitespaceNormalized:
    def test_extra_spaces_match(self):
        content = "hello   world   foo"
        old_text = "hello world foo"
        result = _match_whitespace_normalized(content, old_text, "replaced", 1)
        assert result is not None
        new_content, matched = result
        assert matched == 1
        assert "replaced" in new_content

    def test_tabs_and_newlines_match(self):
        content = "hello\t\tworld\n  foo"
        old_text = "hello world foo"
        result = _match_whitespace_normalized(content, old_text, "X", 1)
        assert result is not None
        _, matched = result
        assert matched == 1

    def test_no_match(self):
        content = "hello world"
        result = _match_whitespace_normalized(content, "goodbye moon", "x", 1)
        assert result is None


class TestIndentationFlexible:
    def test_different_indentation_match(self):
        content = "        def foo():\n        pass\n"
        old_text = "    def foo():\n    pass"
        result = _match_indentation_flexible(content, old_text, "    def bar():\n    return 1", 1)
        assert result is not None
        new_content, matched = result
        assert matched == 1
        assert "bar" in new_content

    def test_no_indentation_vs_indented(self):
        content = "    hello\n    world\n"
        old_text = "hello\nworld"
        result = _match_indentation_flexible(content, old_text, "foo\nbar", 1)
        assert result is not None

    def test_no_match(self):
        content = "    hello\n    world\n"
        result = _match_indentation_flexible(content, "goodbye\nmoon", "x\ny", 1)
        assert result is None


class TestFuzzyFindAndReplace:
    def test_returns_none_strategy_when_no_match(self):
        _, matched, strategy = _fuzzy_find_and_replace("hello", "missing", "x", 1)
        assert matched == 0
        assert strategy == "none"

    def test_line_trimmed_takes_priority(self):
        content = "  hello  \n  world  \n"
        old_text = "hello\nworld"
        _, matched, strategy = _fuzzy_find_and_replace(content, old_text, "x", 1)
        assert matched > 0
        assert strategy == "line_trimmed"


class TestReplaceInFileFuzzy:
    def test_exact_match_takes_priority(self, tmp_path):
        target = tmp_path / "a.txt"
        _write(target, "hello world")
        result = file_editor.replace_in_file(
            str(target), old_text="hello world", new_text="hi", root_dir=tmp_path,
        )
        assert result.success is True
        assert result.metadata.get("fuzzy_strategy") is None

    def test_fuzzy_fallback_on_whitespace_mismatch(self, tmp_path):
        target = tmp_path / "a.py"
        _write(target, "    def foo():\n        pass\n")
        result = file_editor.replace_in_file(
            str(target),
            old_text="def foo():\n    pass",
            new_text="def bar():\n    return 1",
            root_dir=tmp_path,
        )
        assert result.success is True
        assert result.metadata.get("fuzzy_strategy") is not None
        assert "bar" in target.read_text(encoding="utf-8")

    def test_fuzzy_disabled(self, tmp_path):
        target = tmp_path / "a.txt"
        _write(target, "  hello  ")
        result = file_editor.replace_in_file(
            str(target), old_text="hello", new_text="hi", fuzzy=False, root_dir=tmp_path,
        )
        # "hello" without spaces won't match "  hello  " exactly, and fuzzy is off
        # Actually "hello" IS a substring of "  hello  " so exact match works
        assert result.success is True

    def test_fuzzy_disabled_fails_on_mismatch(self, tmp_path):
        target = tmp_path / "a.py"
        _write(target, "    def foo():\n        pass\n")
        result = file_editor.replace_in_file(
            str(target),
            old_text="def foo():\n    pass",
            new_text="def bar():\n    return 1",
            fuzzy=False,
            root_dir=tmp_path,
        )
        assert result.success is False
