"""Unit tests for split_on_markers, split_on_paragraphs, and strip_split_markers."""

import pytest

from src.core.reasoning_tags import split_on_markers, split_on_paragraphs, strip_split_markers


class TestSplitOnMarkers:
    def test_no_marker_returns_single_element(self):
        assert split_on_markers("hello world") == ["hello world"]

    def test_single_marker_splits_into_two(self):
        result = split_on_markers("hello [SPLIT] world")
        assert result == ["hello", "world"]

    def test_multiple_markers(self):
        result = split_on_markers("a [SPLIT] b [SPLIT] c")
        assert result == ["a", "b", "c"]

    def test_case_insensitive(self):
        result = split_on_markers("a [split] b [Split] c")
        assert result == ["a", "b", "c"]

    def test_strips_whitespace_around_segments(self):
        result = split_on_markers("  hello  [SPLIT]  world  ")
        assert result == ["hello", "world"]

    def test_empty_segments_dropped(self):
        result = split_on_markers("[SPLIT] hello [SPLIT]")
        assert result == ["hello"]

    def test_empty_string(self):
        result = split_on_markers("")
        assert result == [""]

    def test_only_marker(self):
        result = split_on_markers("[SPLIT]")
        assert result == []

    def test_adjacent_markers_drop_empty(self):
        result = split_on_markers("a [SPLIT][SPLIT] b")
        assert result == ["a", "b"]

    def test_preserves_content_with_newlines(self):
        result = split_on_markers("line1\nline2 [SPLIT] line3")
        assert result == ["line1\nline2", "line3"]


class TestSplitOnParagraphs:
    def test_no_double_newline_returns_single(self):
        assert split_on_paragraphs("hello world") == ["hello world"]

    def test_single_newline_not_split(self):
        assert split_on_paragraphs("hello\nworld") == ["hello\nworld"]

    def test_double_newline_splits(self):
        result = split_on_paragraphs("hello\n\nworld")
        assert result == ["hello", "world"]

    def test_multiple_paragraphs(self):
        result = split_on_paragraphs("a\n\nb\n\nc")
        assert result == ["a", "b", "c"]

    def test_strips_whitespace(self):
        result = split_on_paragraphs("  hello  \n\n  world  ")
        assert result == ["hello", "world"]

    def test_empty_paragraphs_dropped(self):
        result = split_on_paragraphs("hello\n\n\n\nworld")
        assert result == ["hello", "world"]

    def test_min_segments_respected(self):
        # 只有 1 段，不满足 min_segments=2
        assert split_on_paragraphs("hello") == ["hello"]

    def test_blank_lines_with_spaces(self):
        result = split_on_paragraphs("hello\n   \nworld")
        assert result == ["hello", "world"]


class TestStripSplitMarkers:
    def test_no_marker_unchanged(self):
        assert strip_split_markers("hello world") == "hello world"

    def test_single_marker_removed(self):
        result = strip_split_markers("hello [SPLIT] world")
        assert result == "hello world"

    def test_multiple_markers_removed(self):
        result = strip_split_markers("a [SPLIT] b [SPLIT] c")
        assert result == "a b c"

    def test_case_insensitive(self):
        result = strip_split_markers("a [split] b")
        assert result == "a b"

    def test_collapses_extra_whitespace(self):
        result = strip_split_markers("hello  [SPLIT]  world")
        assert result == "hello world"

    def test_empty_string(self):
        assert strip_split_markers("") == ""

    def test_only_marker(self):
        result = strip_split_markers("[SPLIT]")
        assert result == ""
