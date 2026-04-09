"""CredentialVault 单元测试。"""

from __future__ import annotations

from cryptography.fernet import Fernet

from src.core.credential_vault import Credential, CredentialVault

import pytest


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _make_vault(tmp_path, monkeypatch) -> CredentialVault:
    """创建一个测试用的 CredentialVault 实例。"""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CREDENTIAL_VAULT_KEY", key)
    vault_path = str(tmp_path / "vault.enc")
    return CredentialVault(vault_path=vault_path)


def _sample_credential(service: str = "github") -> Credential:
    return Credential(
        service=service,
        username="kevin@example.com",
        password="super_secret_123",
        login_url="https://github.com/login",
        extra={"org": "lapwing-dev"},
        notes="2FA 用 authenticator app",
    )


# ── 测试 ──────────────────────────────────────────────────────────────────────

class TestSetGetCredential:
    def test_set_get_credential(self, tmp_path, monkeypatch):
        """存储并读取凭据，验证所有字段一致。"""
        vault = _make_vault(tmp_path, monkeypatch)
        cred = _sample_credential()

        vault.set("github", cred)
        result = vault.get("github")

        assert result is not None
        assert result.service == "github"
        assert result.username == cred.username
        assert result.password == cred.password
        assert result.login_url == cred.login_url
        assert result.extra == cred.extra
        assert result.notes == cred.notes

    def test_get_nonexistent(self, tmp_path, monkeypatch):
        """读取不存在的服务返回 None。"""
        vault = _make_vault(tmp_path, monkeypatch)
        assert vault.get("nonexistent") is None


class TestEncryption:
    def test_encryption(self, tmp_path, monkeypatch):
        """保险库文件在磁盘上是加密的，不能直接看到明文密码。"""
        vault = _make_vault(tmp_path, monkeypatch)
        cred = _sample_credential()
        vault.set("github", cred)

        vault_path = tmp_path / "vault.enc"
        raw = vault_path.read_bytes()

        # 原始字节中不应包含明文密码
        assert b"super_secret_123" not in raw
        # 也不应包含用户名
        assert b"kevin@example.com" not in raw


class TestDelete:
    def test_delete_existing(self, tmp_path, monkeypatch):
        """删除已存在的服务返回 True。"""
        vault = _make_vault(tmp_path, monkeypatch)
        vault.set("github", _sample_credential())

        assert vault.delete("github") is True
        assert vault.get("github") is None

    def test_delete_nonexistent(self, tmp_path, monkeypatch):
        """删除不存在的服务返回 False。"""
        vault = _make_vault(tmp_path, monkeypatch)
        assert vault.delete("nonexistent") is False


class TestListServices:
    def test_list_services(self, tmp_path, monkeypatch):
        """存储多个服务后，list_services 返回所有名称。"""
        vault = _make_vault(tmp_path, monkeypatch)
        vault.set("github", _sample_credential("github"))
        vault.set("gitlab", _sample_credential("gitlab"))
        vault.set("bitbucket", _sample_credential("bitbucket"))

        services = vault.list_services()
        assert sorted(services) == ["bitbucket", "github", "gitlab"]

    def test_list_empty(self, tmp_path, monkeypatch):
        """空保险库的 list_services 返回空列表。"""
        vault = _make_vault(tmp_path, monkeypatch)
        assert vault.list_services() == []


class TestMissingKey:
    def test_missing_key(self, tmp_path, monkeypatch):
        """环境变量未设置时，构造函数抛出 ValueError。"""
        monkeypatch.delenv("CREDENTIAL_VAULT_KEY", raising=False)
        with pytest.raises(ValueError, match="CREDENTIAL_VAULT_KEY 环境变量未设置"):
            CredentialVault(vault_path=str(tmp_path / "vault.enc"))

    def test_empty_key(self, tmp_path, monkeypatch):
        """环境变量为空字符串时，同样抛出 ValueError。"""
        monkeypatch.setenv("CREDENTIAL_VAULT_KEY", "")
        with pytest.raises(ValueError, match="CREDENTIAL_VAULT_KEY 环境变量未设置"):
            CredentialVault(vault_path=str(tmp_path / "vault.enc"))


class TestCorruptedVault:
    def test_corrupted_vault(self, tmp_path, monkeypatch):
        """保险库文件损坏时，不崩溃，使用空保险库继续工作。"""
        key = Fernet.generate_key().decode()
        monkeypatch.setenv("CREDENTIAL_VAULT_KEY", key)

        vault_path = tmp_path / "vault.enc"
        vault_path.write_bytes(b"this is garbage data, not valid fernet")

        # 不应抛出异常
        vault = CredentialVault(vault_path=str(vault_path))

        # 应当是空的保险库
        assert vault.list_services() == []

        # 应当可以正常写入新数据
        vault.set("github", _sample_credential())
        assert vault.get("github") is not None


class TestGenerateKey:
    def test_generate_key(self):
        """生成的密钥可以被 Fernet 接受。"""
        key = CredentialVault.generate_key()

        # 应该是字符串
        assert isinstance(key, str)

        # 应该可以用来创建 Fernet 实例（不抛异常即有效）
        f = Fernet(key.encode())

        # 验证可以加解密
        plaintext = b"test data"
        encrypted = f.encrypt(plaintext)
        assert f.decrypt(encrypted) == plaintext

    def test_generate_key_unique(self):
        """每次生成的密钥不同。"""
        key1 = CredentialVault.generate_key()
        key2 = CredentialVault.generate_key()
        assert key1 != key2
