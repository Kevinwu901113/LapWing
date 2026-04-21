"""CredentialVault — 加密凭据保险库。

为 browser_login 工具提供安全的网站登录凭据存储。
使用 Fernet 对称加密，LLM 永远看不到明文密码。

存储格式（加密前）:
{
    "version": 1,
    "services": {
        "github": {
            "username": "kevin@example.com",
            "password": "xxx",
            "login_url": "https://github.com/login",
            "extra": null,
            "notes": "2FA 用 authenticator app"
        }
    }
}
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("lapwing.core.credential_vault")


# ── 数据模型 ─────────────────────────────────────────────────────────────────

@dataclass
class Credential:
    """单个服务的登录凭据。"""
    service: str
    username: str
    password: str
    login_url: str
    extra: dict | None = None
    notes: str | None = None


# ── 加密保险库 ────────────────────────────────────────────────────────────────

class CredentialVault:
    """Fernet 加密的凭据保险库。

    凭据以加密 JSON 文件存储在磁盘上。每次读写都会加密/解密整个文件。
    密钥从环境变量读取，永远不会持久化到磁盘。
    """

    def __init__(self, vault_path: str = "data/credentials/vault.enc",
                 key_env: str = "CREDENTIAL_VAULT_KEY") -> None:
        # 直接读 os.environ：加密密钥是安全敏感值，key_env 名称由调用方指定，不走 settings
        key = os.environ.get(key_env)
        if not key:
            raise ValueError(f"{key_env} 环境变量未设置")

        self._fernet = Fernet(key.encode())
        self._vault_path = Path(vault_path)

        # 确保父目录存在
        self._vault_path.parent.mkdir(parents=True, exist_ok=True)

        # 加载已有数据（如果存在）
        self._data = self._load()

    def get(self, service: str) -> Credential | None:
        """解密并返回指定服务的凭据，不存在则返回 None。"""
        svc = self._data["services"].get(service)
        if svc is None:
            return None
        return Credential(
            service=service,
            username=svc["username"],
            password=svc["password"],
            login_url=svc["login_url"],
            extra=svc.get("extra"),
            notes=svc.get("notes"),
        )

    def set(self, service: str, credential: Credential) -> None:
        """加密并保存凭据。如果服务已存在则覆盖。"""
        entry = asdict(credential)
        # service 名称作为 key，不重复存储在 value 中
        entry.pop("service", None)
        self._data["services"][service] = entry
        self._save()
        logger.info("凭据已保存: %s", service)

    def delete(self, service: str) -> bool:
        """删除指定服务的凭据。返回 True 表示确实删除了，False 表示不存在。"""
        if service not in self._data["services"]:
            return False
        del self._data["services"][service]
        self._save()
        logger.info("凭据已删除: %s", service)
        return True

    def list_services(self) -> list[str]:
        """返回所有已存储的服务名称列表（不含凭据内容）。"""
        return list(self._data["services"].keys())

    @staticmethod
    def generate_key() -> str:
        """生成一个新的 Fernet 密钥（URL-safe base64 字符串）。"""
        return Fernet.generate_key().decode()

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _empty_vault(self) -> dict:
        """返回空的保险库数据结构。"""
        return {"version": 1, "services": {}}

    def _load(self) -> dict:
        """从磁盘加载并解密保险库数据。

        - 文件不存在 → 返回空保险库
        - 文件损坏/解密失败 → 记录警告，返回空保险库
        """
        if not self._vault_path.exists():
            logger.debug("保险库文件不存在，将在首次写入时创建: %s", self._vault_path)
            return self._empty_vault()

        try:
            encrypted = self._vault_path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted)
            data = json.loads(decrypted.decode())
            logger.debug("保险库已加载，共 %d 个服务", len(data.get("services", {})))
            return data
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("保险库文件损坏或密钥不匹配，将使用空保险库: %s", exc)
            return self._empty_vault()

    def _save(self) -> None:
        """加密并写入保险库数据到磁盘。"""
        plaintext = json.dumps(self._data, ensure_ascii=False).encode()
        encrypted = self._fernet.encrypt(plaintext)
        self._vault_path.write_bytes(encrypted)
        logger.debug("保险库已写入磁盘: %s", self._vault_path)
