"""VitalGuard 单元测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.vital_guard import (
    BACKUP_DIR,
    VITAL_PATHS,
    GuardResult,
    Verdict,
    auto_backup,
    check,
    check_compound,
    _is_vital,
)
from config.settings import ROOT_DIR


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _assert_pass(cmd: str) -> None:
    result = check_compound(cmd)
    assert result.verdict == Verdict.PASS, f"期望 PASS，实际 {result.verdict}，命令: {cmd!r}"


def _assert_block(cmd: str) -> None:
    result = check_compound(cmd)
    assert result.verdict == Verdict.BLOCK, f"期望 BLOCK，实际 {result.verdict}，命令: {cmd!r}"


def _assert_verify(cmd: str) -> None:
    result = check_compound(cmd)
    assert result.verdict == Verdict.VERIFY_FIRST, f"期望 VERIFY_FIRST，实际 {result.verdict}，命令: {cmd!r}"


# ── PASS 场景 ─────────────────────────────────────────────────────────────────

class TestPass:
    def test_safe_ls(self):
        _assert_pass("ls -la /tmp")

    def test_safe_pwd(self):
        _assert_pass("pwd")

    def test_safe_echo(self):
        _assert_pass("echo hello world")

    def test_safe_pip_install(self):
        _assert_pass("pip install flask")

    def test_safe_cat_tmp(self):
        _assert_pass("cat /tmp/foo.txt")

    def test_safe_rm_tmp(self):
        _assert_pass("rm /tmp/some_file.txt")

    def test_safe_docker(self):
        _assert_pass("docker ps -a")

    def test_safe_git_log(self):
        _assert_pass("git log --oneline -10")

    def test_safe_curl(self):
        _assert_pass("curl -s https://api.example.com/data")

    def test_empty_command(self):
        _assert_pass("")
        _assert_pass("   ")


# ── BLOCK 场景 ────────────────────────────────────────────────────────────────

class TestBlock:
    def test_fork_bomb(self):
        _assert_block(":(){ :|:& };:")

    def test_rm_rf_root(self):
        _assert_block("rm -rf /")

    def test_rm_rf_root_wildcard(self):
        _assert_block("rm -rf /*")

    def test_rm_rf_home_tilde(self):
        _assert_block("rm -rf ~")

    def test_mkfs(self):
        _assert_block("mkfs.ext4 /dev/sdb")

    def test_dd_to_disk(self):
        _assert_block("dd if=/dev/zero of=/dev/sda")

    def test_systemctl_stop_lapwing(self):
        _assert_block("systemctl stop lapwing")

    def test_systemctl_disable_lapwing(self):
        _assert_block("systemctl disable lapwing")

    def test_pkill_lapwing(self):
        _assert_block("pkill lapwing")

    def test_kill_lapwing(self):
        _assert_block("kill lapwing")

    def test_rm_src_core(self):
        """删除 src/ 核心目录下的文件 → BLOCK"""
        src_dir = ROOT_DIR / "src" / "core" / "brain.py"
        _assert_block(f"rm {src_dir}")

    def test_rm_rf_src(self):
        """删除整个 src/ → BLOCK"""
        src_dir = ROOT_DIR / "src"
        _assert_block(f"rm -rf {src_dir}")

    def test_rm_prompts(self):
        prompts_dir = ROOT_DIR / "prompts"
        _assert_block(f"rm -rf {prompts_dir}")

    def test_rm_constitution(self):
        constitution = ROOT_DIR / "data" / "identity" / "constitution.md"
        _assert_block(f"rm {constitution}")

    def test_rm_memory_dir(self):
        memory_dir = ROOT_DIR / "data" / "memory"
        _assert_block(f"rm -rf {memory_dir}")

    def test_sed_constitution(self):
        """通过 sed 修改宪法 → BLOCK"""
        constitution = ROOT_DIR / "data" / "identity" / "constitution.md"
        _assert_block(f"sed -i 's/foo/bar/' {constitution}")

    def test_write_to_constitution_redirect(self):
        """通过重定向写入宪法 → BLOCK"""
        constitution = ROOT_DIR / "data" / "identity" / "constitution.md"
        _assert_block(f"echo '' > {constitution}")

    def test_vital_guard_self_protection(self):
        """不能修改 VitalGuard 自身 → BLOCK"""
        vital_guard_path = Path(__file__).parent.parent.parent / "src" / "core" / "vital_guard.py"
        _assert_block(f"cp new_guard.py {vital_guard_path}")

    def test_compound_with_dangerous(self):
        """复合命令中有危险子命令 → BLOCK"""
        src_dir = ROOT_DIR / "src"
        _assert_block(f"ls && rm -rf {src_dir}")

    def test_pipe_to_tee_constitution(self):
        """管道写入宪法 → BLOCK（通过 tee）"""
        constitution = ROOT_DIR / "data" / "identity" / "constitution.md"
        _assert_block(f"echo bad content | tee {constitution}")


# ── VERIFY_FIRST 场景 ─────────────────────────────────────────────────────────

class TestVerifyFirst:
    def test_cp_to_src(self):
        """拷贝文件到 src/ → VERIFY_FIRST"""
        target = ROOT_DIR / "src" / "core" / "brain.py"
        _assert_verify(f"cp new_brain.py {target}")

    def test_mv_to_config(self):
        """移动文件到 config/ → VERIFY_FIRST"""
        target = ROOT_DIR / "config" / "settings.py"
        _assert_verify(f"mv new_settings.py {target}")

    def test_redirect_to_src(self):
        """重定向写入 src/ 非宪法文件 → VERIFY_FIRST"""
        target = ROOT_DIR / "src" / "core" / "brain.py"
        _assert_verify(f"cat new.py > {target}")

    def test_pip_upgrade(self):
        """pip install --upgrade → VERIFY_FIRST"""
        _assert_verify("pip install --upgrade flask")

    def test_pip_upgrade_short_flag(self):
        """pip install -U → VERIFY_FIRST"""
        _assert_verify("pip install -U requests")

    def test_compound_with_verify(self):
        """复合命令中有 VERIFY_FIRST，无 BLOCK → 整体 VERIFY_FIRST"""
        target = ROOT_DIR / "src" / "core" / "brain.py"
        _assert_verify(f"ls && cp new.py {target}")


# ── _is_vital 路径检查 ────────────────────────────────────────────────────────

class TestIsVital:
    def test_src_subdir(self):
        assert _is_vital(ROOT_DIR / "src" / "core" / "brain.py")

    def test_src_root(self):
        assert _is_vital(ROOT_DIR / "src")

    def test_prompts(self):
        assert _is_vital(ROOT_DIR / "prompts" / "system.md")

    def test_config(self):
        assert _is_vital(ROOT_DIR / "config" / "settings.py")

    def test_constitution(self):
        assert _is_vital(ROOT_DIR / "data" / "identity" / "constitution.md")

    def test_main_py(self):
        assert _is_vital(ROOT_DIR / "main.py")

    def test_tmp_not_vital(self):
        assert not _is_vital(Path("/tmp/foo.txt"))

    def test_data_db_not_vital(self):
        # lapwing.db 不在 VITAL_PATHS 中（CLAUDE.md 中列为 BLOCK，但通过 rm 模式拦截）
        assert not _is_vital(ROOT_DIR / "data" / "lapwing.db")

    def test_system_etc(self):
        assert _is_vital(Path("/etc/passwd"))

    def test_system_boot(self):
        assert _is_vital(Path("/boot/grub/grub.cfg"))


# ── auto_backup 测试 ──────────────────────────────────────────────────────────

class TestAutoBackup:
    @pytest.mark.asyncio
    async def test_backup_creates_directory(self, tmp_path):
        """备份应创建目标目录并复制文件。"""
        # 创建一个临时文件
        test_file = tmp_path / "test.py"
        test_file.write_text("# test content")

        # patch BACKUP_DIR 到临时目录
        backup_base = tmp_path / "backups"
        with patch("src.core.vital_guard.BACKUP_DIR", backup_base):
            backup_path = await auto_backup([test_file])

        assert backup_path.exists()
        # 文件应该被复制到备份目录中（按相对路径或绝对路径）
        assert backup_path.is_dir()
        # 备份目录中应有文件
        all_files = list(backup_path.rglob("*"))
        assert len(all_files) > 0, "备份目录不应为空"

    @pytest.mark.asyncio
    async def test_backup_prunes_old(self, tmp_path):
        """超过 50 个备份时应清理旧的。"""
        backup_base = tmp_path / "backups"
        backup_base.mkdir(parents=True)

        # 创建 55 个假备份目录
        for i in range(55):
            (backup_base / f"2024010{i:02d}_120000").mkdir()

        test_file = tmp_path / "test.py"
        test_file.write_text("# test")

        with patch("src.core.vital_guard.BACKUP_DIR", backup_base):
            await auto_backup([test_file])

        remaining = list(backup_base.iterdir())
        assert len(remaining) <= 50, f"应保留不超过 50 个备份，实际有 {len(remaining)} 个"

    @pytest.mark.asyncio
    async def test_backup_nonexistent_file(self, tmp_path):
        """不存在的文件应静默跳过，不抛异常。"""
        nonexistent = tmp_path / "ghost.py"
        backup_base = tmp_path / "backups"

        with patch("src.core.vital_guard.BACKUP_DIR", backup_base):
            backup_path = await auto_backup([nonexistent])

        assert backup_path.is_dir()
