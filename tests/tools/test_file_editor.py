"""file_editor 单元测试。"""

from pathlib import Path

from src.tools import file_editor


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
