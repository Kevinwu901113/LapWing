"""文件快照管理器——shadow git repo 自动回滚。

在 file_write / execute_shell 修改文件前自动创建快照。
每个 conversation turn 最多一次快照。
用 GIT_DIR + GIT_WORK_TREE 分离，不污染用户项目。

快照存储：data/checkpoints/{dir_hash}/
"""

import hashlib
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("lapwing.core.checkpoint")

CHECKPOINT_BASE = Path("data/checkpoints")
GIT_TIMEOUT = 30  # 秒

DEFAULT_EXCLUDES = [
    "node_modules/", "dist/", "build/", "__pycache__/",
    "*.pyc", ".DS_Store", "*.log", ".cache/",
    ".venv/", "venv/", ".git/",
]


class CheckpointManager:
    """透明文件快照。LLM 不可见，不是工具。"""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or CHECKPOINT_BASE
        self._snapshots_this_turn: set[str] = set()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def new_turn(self):
        """每个 conversation turn 开始时重置。"""
        self._snapshots_this_turn.clear()

    def snapshot(self, working_dir: str) -> str | None:
        """为 working_dir 创建快照。每个 turn 每个目录最多一次。

        Returns:
            commit hash 或 None（如果跳过/失败）
        """
        abs_dir = os.path.abspath(working_dir)
        if abs_dir in self._snapshots_this_turn:
            return None  # 这个 turn 已经快照过
        if not os.path.isdir(abs_dir):
            return None

        git_dir = self._get_shadow_dir(abs_dir)
        self._ensure_shadow_repo(git_dir, abs_dir)

        try:
            env = {**os.environ, "GIT_DIR": str(git_dir), "GIT_WORK_TREE": abs_dir}
            # Stage all changes
            subprocess.run(
                ["git", "add", "-A"],
                env=env, timeout=GIT_TIMEOUT,
                capture_output=True, check=True,
            )
            # Check if there are changes to commit
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                env=env, timeout=GIT_TIMEOUT,
                capture_output=True,
            )
            if result.returncode == 0:
                return None  # 没有变更

            # Commit
            subprocess.run(
                ["git", "commit", "-m", "auto-checkpoint"],
                env=env, timeout=GIT_TIMEOUT,
                capture_output=True, text=True,
            )
            # 获取 commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                env=env, timeout=GIT_TIMEOUT,
                capture_output=True, text=True, check=True,
            )
            commit_hash = hash_result.stdout.strip()
            self._snapshots_this_turn.add(abs_dir)
            logger.info("快照创建: dir=%s hash=%s", abs_dir, commit_hash[:8])
            return commit_hash

        except Exception as e:
            logger.warning("快照失败（非致命）: %s", e)
            return None

    def rollback(self, working_dir: str, commit_hash: str) -> bool:
        """回滚到指定快照。"""
        abs_dir = os.path.abspath(working_dir)
        git_dir = self._get_shadow_dir(abs_dir)
        if not git_dir.exists():
            return False
        try:
            env = {**os.environ, "GIT_DIR": str(git_dir), "GIT_WORK_TREE": abs_dir}
            subprocess.run(
                ["git", "checkout", commit_hash, "--", "."],
                env=env, timeout=GIT_TIMEOUT,
                capture_output=True, check=True,
            )
            logger.info("回滚成功: dir=%s hash=%s", abs_dir, commit_hash[:8])
            return True
        except Exception as e:
            logger.warning("回滚失败: %s", e)
            return False

    def list_checkpoints(self, working_dir: str, limit: int = 10) -> list[dict]:
        """列出快照历史。"""
        abs_dir = os.path.abspath(working_dir)
        git_dir = self._get_shadow_dir(abs_dir)
        if not git_dir.exists():
            return []
        try:
            env = {**os.environ, "GIT_DIR": str(git_dir), "GIT_WORK_TREE": abs_dir}
            result = subprocess.run(
                ["git", "log", f"--max-count={limit}", "--format=%H %ai"],
                env=env, timeout=GIT_TIMEOUT,
                capture_output=True, text=True, check=True,
            )
            return [
                {"hash": line.split()[0], "time": " ".join(line.split()[1:])}
                for line in result.stdout.strip().splitlines()
                if line.strip()
            ]
        except Exception:
            return []

    def _get_shadow_dir(self, abs_dir: str) -> Path:
        dir_hash = hashlib.sha256(abs_dir.encode()).hexdigest()[:16]
        return self._base_dir / dir_hash

    def _ensure_shadow_repo(self, git_dir: Path, work_tree: str):
        if (git_dir / "HEAD").exists():
            return
        git_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "--bare", str(git_dir)],
            timeout=GIT_TIMEOUT,
            capture_output=True, check=True,
        )
        # 配置 committer 身份（shadow repo 只做内部快照）
        env = {**os.environ, "GIT_DIR": str(git_dir), "GIT_WORK_TREE": work_tree}
        subprocess.run(
            ["git", "config", "user.email", "checkpoint@lapwing.local"],
            env=env, timeout=GIT_TIMEOUT, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Lapwing Checkpoint"],
            env=env, timeout=GIT_TIMEOUT, capture_output=True,
        )
        # 写默认排除
        exclude_file = git_dir / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        exclude_file.write_text("\n".join(DEFAULT_EXCLUDES) + "\n")
        # 记录工作目录
        (git_dir / "LAPWING_WORKDIR").write_text(work_tree)
