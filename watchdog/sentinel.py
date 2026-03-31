#!/usr/bin/env python3
"""Lapwing Sentinel — 独立哨兵进程。

这是一个极简的看门狗，与 Lapwing 主代码完全解耦。
即使 Lapwing 的 src/ 被全部删除，本进程依然能从备份恢复。

职责：
1. 每 5 分钟检查核心文件完整性（对比 SHA256 manifest）
2. 发现缺失或被篡改的文件时，从最新备份恢复
3. 恢复后重启 Lapwing 服务
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── 路径（不依赖 src/，直接硬编码相对路径）──────────────────────────────────────
_SENTINEL_DIR = Path(__file__).parent
LAPWING_ROOT = _SENTINEL_DIR.parent

MANIFEST_PATH = LAPWING_ROOT / "data" / "vital_manifest.json"
BACKUP_DIR = LAPWING_ROOT / "data" / "backups" / "vital_guard"

CHECK_INTERVAL = 300  # 5 分钟

# ── Manifest 操作 ──────────────────────────────────────────────────────────────

def generate_manifest() -> dict[str, str]:
    """扫描关键文件，生成 SHA256 hash 清单。

    注意：此函数有意与 src/core/vital_guard.py 中的同名函数重复。
    Sentinel 必须完全独立，即使 src/ 被删除也能工作，因此不能 import 主代码。
    """
    manifest: dict[str, str] = {}
    critical_dirs = ["src", "prompts", "config"]
    critical_files = ["main.py", "data/identity/constitution.md"]

    for d in critical_dirs:
        dir_path = LAPWING_ROOT / d
        if not dir_path.exists():
            continue
        for ext in ("*.py", "*.md", "*.json", "*.yaml", "*.yml"):
            for f in dir_path.rglob(ext):
                try:
                    rel = str(f.relative_to(LAPWING_ROOT))
                    manifest[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
                except Exception:
                    pass

    for f_str in critical_files:
        fp = LAPWING_ROOT / f_str
        if fp.exists():
            try:
                manifest[f_str] = hashlib.sha256(fp.read_bytes()).hexdigest()
            except Exception:
                pass

    return manifest


def save_manifest(manifest: dict[str, str]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


def load_manifest() -> dict[str, str]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except Exception:
        return {}


# ── 完整性检查 ─────────────────────────────────────────────────────────────────

def check_integrity() -> list[str]:
    """
    对比当前文件与 manifest 中保存的状态。

    Returns:
        问题列表，格式为 "MISSING: path" 或 "MODIFIED: path"
    """
    saved = load_manifest()
    if not saved:
        return []

    issues: list[str] = []
    for path_str, expected_hash in saved.items():
        fp = LAPWING_ROOT / path_str
        if not fp.exists():
            issues.append(f"MISSING: {path_str}")
        else:
            try:
                actual = hashlib.sha256(fp.read_bytes()).hexdigest()
                if actual != expected_hash:
                    issues.append(f"MODIFIED: {path_str}")
            except Exception:
                issues.append(f"UNREADABLE: {path_str}")

    return issues


# ── 恢复 ───────────────────────────────────────────────────────────────────────

def restore_from_backup(issues: list[str]) -> int:
    """
    从最新备份恢复缺失或被修改的文件。

    Returns:
        成功恢复的文件数量
    """
    if not BACKUP_DIR.exists():
        print("[Sentinel] 没有备份目录，无法恢复。", flush=True)
        return 0

    backups = sorted(BACKUP_DIR.iterdir(), reverse=True)
    if not backups:
        print("[Sentinel] 没有可用备份。", flush=True)
        return 0

    latest = backups[0]
    restored = 0

    for entry in issues:
        # entry 格式: "MISSING: src/core/brain.py" 或 "MODIFIED: ..."
        parts = entry.split(": ", 1)
        if len(parts) != 2:
            continue
        _, path_str = parts
        backup_file = latest / path_str
        target = LAPWING_ROOT / path_str

        if backup_file.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if backup_file.is_dir():
                    shutil.copytree(backup_file, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(backup_file, target)
                print(f"[Sentinel] 已恢复: {path_str} (来自 {latest.name})", flush=True)
                restored += 1
            except Exception as e:
                print(f"[Sentinel] 恢复失败 {path_str}: {e}", flush=True)
        else:
            print(f"[Sentinel] 备份中未找到: {path_str}", flush=True)

    return restored


# ── 重启 Lapwing ──────────────────────────────────────────────────────────────

def restart_lapwing() -> None:
    """通过 systemd user service 重启 Lapwing。"""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "restart", "lapwing"],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            print("[Sentinel] Lapwing 服务已重启。", flush=True)
        else:
            print(f"[Sentinel] 重启失败: {result.stderr.decode()}", flush=True)
    except Exception as e:
        print(f"[Sentinel] 无法重启服务: {e}", flush=True)


# ── 主循环 ────────────────────────────────────────────────────────────────────

def main() -> None:
    print("[Sentinel] 启动，检查间隔 5 分钟。", flush=True)

    # 如果没有 manifest，先生成一个
    if not MANIFEST_PATH.exists():
        print("[Sentinel] 未找到 manifest，正在生成...", flush=True)
        manifest = generate_manifest()
        save_manifest(manifest)
        print(f"[Sentinel] Manifest 已生成，共 {len(manifest)} 个文件。", flush=True)

    while True:
        try:
            issues = check_integrity()

            if issues:
                print(f"[Sentinel] 检测到 {len(issues)} 个问题:", flush=True)
                for issue in issues:
                    print(f"  {issue}", flush=True)

                restored = restore_from_backup(issues)

                if restored > 0:
                    restart_lapwing()
                    # 等待重启完成后更新 manifest
                    time.sleep(30)
                    new_manifest = generate_manifest()
                    save_manifest(new_manifest)
                    print("[Sentinel] Manifest 已更新。", flush=True)
            else:
                pass  # 一切正常，静默

        except Exception as e:
            print(f"[Sentinel] 错误: {e}", flush=True)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
