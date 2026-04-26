"""tests/core/test_vital_guard_v2.py — 极简 VitalGuard 测试。"""

from pathlib import Path

from src.core.vital_guard import Verdict, check, check_compound, check_file_target


class TestCheck:
    def test_pass_safe_command(self):
        result = check("ls -la /tmp")
        assert result.verdict == Verdict.PASS

    def test_block_rm_rf_root(self):
        result = check("rm -rf /")
        assert result.verdict == Verdict.BLOCK

    def test_block_mkfs(self):
        result = check("mkfs.ext4 /dev/sda1")
        assert result.verdict == Verdict.BLOCK

    def test_block_dd_device(self):
        result = check("dd if=/dev/zero of=/dev/sda bs=1M")
        assert result.verdict == Verdict.BLOCK

    def test_block_src_path(self):
        result = check("rm -rf src/core/brain.py")
        assert result.verdict == Verdict.BLOCK

    def test_pass_readonly_locked_src_path(self):
        result = check('grep -R "QQAdapter" /home/kevin/lapwing/src')
        assert result.verdict == Verdict.PASS

    def test_pass_python_import_from_locked_src(self):
        result = check(
            'python3 -c "from src.adapters.qq_adapter import QQAdapter; print(QQAdapter)"'
        )
        assert result.verdict == Verdict.PASS

    def test_block_redirect_to_locked_src_path(self):
        result = check("echo patched > src/core/brain.py")
        assert result.verdict == Verdict.BLOCK

    def test_pass_empty(self):
        result = check("")
        assert result.verdict == Verdict.PASS


class TestCheckCompound:
    def test_compound_with_block(self):
        result = check_compound("echo hello && rm -rf /")
        assert result.verdict == Verdict.BLOCK

    def test_compound_all_pass(self):
        result = check_compound("echo hello && echo world")
        assert result.verdict == Verdict.PASS


class TestCheckFileTarget:
    def test_block_locked_path(self):
        path = Path("data/identity/constitution.md").resolve()
        result = check_file_target(path)
        assert result.verdict == Verdict.BLOCK

    def test_pass_safe_path(self):
        result = check_file_target(Path("/tmp/test.txt"))
        assert result.verdict == Verdict.PASS
