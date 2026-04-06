"""Unit tests for split_on_markers and strip_split_markers."""

import pytest

from src.core.reasoning_tags import split_on_markers, strip_split_markers


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
