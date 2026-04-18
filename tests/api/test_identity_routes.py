"""tests/api/test_identity_routes.py — identity 路由烟雾测试。

三个身份文件（soul / voice / constitution）的 GET / PUT / history / diff /
rollback 端点行为完全对齐。voice.md 写入后 prompt_loader 缓存要被清空。
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import identity as identity_routes
from src.core.identity_file_manager import IdentityFileManager


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    # 隔离 prompt_loader 缓存：用 fake module 记录 clear_cache 调用。
    from src.core import prompt_loader

    cache_clears: list[int] = []
    original_clear = prompt_loader.clear_cache

    def tracked_clear():
        cache_clears.append(1)
        original_clear()

    monkeypatch.setattr(prompt_loader, "clear_cache", tracked_clear)

    identity_dir = tmp_path / "identity"
    prompts_dir = tmp_path / "prompts"
    identity_dir.mkdir()
    prompts_dir.mkdir()

    (identity_dir / "soul.md").write_text("soul 原始内容", encoding="utf-8")
    (identity_dir / "constitution.md").write_text("宪法原始内容", encoding="utf-8")
    (prompts_dir / "lapwing_voice.md").write_text("voice 原始内容", encoding="utf-8")

    soul_mgr = IdentityFileManager(
        file_path=identity_dir / "soul.md",
        snapshot_dir=identity_dir / "soul_snapshots",
        kind="soul",
    )
    voice_mgr = IdentityFileManager(
        file_path=prompts_dir / "lapwing_voice.md",
        snapshot_dir=identity_dir / "voice_snapshots",
        kind="voice",
        on_after_write=tracked_clear,
    )
    const_mgr = IdentityFileManager(
        file_path=identity_dir / "constitution.md",
        snapshot_dir=identity_dir / "constitution_snapshots",
        kind="constitution",
    )

    identity_routes.init(
        soul_manager=soul_mgr,
        voice_manager=voice_mgr,
        constitution_manager=const_mgr,
    )

    app = FastAPI()
    app.include_router(identity_routes.router)
    return TestClient(app), cache_clears, prompts_dir / "lapwing_voice.md"


class TestGetPut:
    @pytest.mark.parametrize("filename,expected", [
        ("soul.md", "soul 原始内容"),
        ("voice.md", "voice 原始内容"),
        ("constitution.md", "宪法原始内容"),
    ])
    def test_get_returns_content(self, client, filename, expected):
        tc, _, _ = client
        r = tc.get(f"/api/v2/identity/{filename}")
        assert r.status_code == 200
        assert r.json() == {"filename": filename, "content": expected}

    def test_get_unknown_file_404(self, client):
        tc, _, _ = client
        r = tc.get("/api/v2/identity/random.md")
        assert r.status_code == 404

    @pytest.mark.parametrize("filename", ["soul.md", "voice.md", "constitution.md"])
    def test_put_writes_and_snapshots(self, client, filename):
        tc, _, _ = client
        r = tc.put(f"/api/v2/identity/{filename}", json={"content": "新内容 " + filename})
        assert r.status_code == 200
        assert r.json()["success"] is True
        # 再读一次：内容已经更新
        r2 = tc.get(f"/api/v2/identity/{filename}")
        assert r2.json()["content"] == "新内容 " + filename


class TestVoiceCacheInvalidation:
    def test_put_voice_clears_prompt_cache(self, client):
        tc, cache_clears, _ = client
        cache_clears.clear()
        r = tc.put("/api/v2/identity/voice.md", json={"content": "## 新说话方式"})
        assert r.status_code == 200
        # on_after_write 被触发 → clear_cache 被调用
        assert cache_clears == [1]

    def test_voice_put_writes_to_prompts_dir(self, client):
        tc, _, voice_path = client
        tc.put("/api/v2/identity/voice.md", json={"content": "HELLO VOICE"})
        # 物理文件必须落在 prompts/lapwing_voice.md（修复 server.py 路径 bug 的验收条件）
        assert voice_path.read_text(encoding="utf-8") == "HELLO VOICE"

    def test_prompt_loader_sees_new_content_after_put(self, client, tmp_path, monkeypatch):
        """PUT 之后 load_prompt 能读到新内容（cache 被清掉，重新从磁盘读）。"""
        from src.core import prompt_loader
        from config import settings

        tc, _, voice_path = client
        # 把 PROMPTS_DIR 临时指到 voice 文件所在目录，让 load_prompt 能找到它。
        monkeypatch.setattr(settings, "PROMPTS_DIR", voice_path.parent)
        monkeypatch.setattr(prompt_loader, "PROMPTS_DIR", voice_path.parent)

        # 先加载一次，灌进缓存
        first = prompt_loader.load_prompt("lapwing_voice")
        assert "voice 原始内容" in first

        # PUT 新内容
        tc.put("/api/v2/identity/voice.md", json={"content": "彻底新的 voice"})

        # 再 load，应该读到新内容（cache 已被 on_after_write 清掉）
        second = prompt_loader.load_prompt("lapwing_voice")
        assert second == "彻底新的 voice"


class TestHistoryDiffRollback:
    @pytest.mark.parametrize("base,filename", [
        ("soul", "soul.md"),
        ("voice", "voice.md"),
        ("constitution", "constitution.md"),
    ])
    def test_history_diff_rollback_roundtrip(self, client, base, filename):
        tc, _, _ = client
        # 写一次形成快照
        tc.put(f"/api/v2/identity/{filename}", json={"content": "v1"})
        tc.put(f"/api/v2/identity/{filename}", json={"content": "v2"})

        r_hist = tc.get(f"/api/v2/identity/{base}/history")
        assert r_hist.status_code == 200
        snaps = r_hist.json()["snapshots"]
        assert len(snaps) >= 2

        snap_id = snaps[-1]["snapshot_id"]  # 最早的那一个 = 原始内容
        assert snap_id.startswith(f"{base}_")

        r_diff = tc.get(f"/api/v2/identity/{base}/diff/{snap_id}")
        assert r_diff.status_code == 200
        assert "diff" in r_diff.json()

        r_rb = tc.post(f"/api/v2/identity/{base}/rollback/{snap_id}")
        assert r_rb.status_code == 200
        assert r_rb.json()["success"] is True

        # 回滚后内容应该是快照的内容（第一个 PUT 之前的初始内容）
        r_after = tc.get(f"/api/v2/identity/{filename}")
        assert r_after.json()["content"] in {"soul 原始内容", "voice 原始内容", "宪法原始内容"}

    def test_rollback_missing_returns_404(self, client):
        tc, _, _ = client
        r = tc.post("/api/v2/identity/voice/rollback/voice_9999_000")
        assert r.status_code == 404

    def test_unknown_base_returns_404(self, client):
        tc, _, _ = client
        r = tc.get("/api/v2/identity/foobar/history")
        assert r.status_code == 404
