"""模型路由配置管理。

配置持久化到 data/config/model_routing.json。
提供注册 provider、分配 slot 的 CRUD 操作。
启动时如果配置文件不存在，从 .env 环境变量迁移生成初始配置。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config.settings import DATA_DIR

logger = logging.getLogger("lapwing.core.model_config")

CONFIG_PATH = Path(DATA_DIR) / "config" / "model_routing.json"

# 所有合法 slot 名称 + 描述（给前端展示用）
SLOT_DEFINITIONS: dict[str, dict[str, str]] = {
    "main_conversation": {
        "name": "主对话",
        "description": "Lapwing 和 Kevin 的核心对话，需要 tool calling 能力",
        "requires_tools": "true",
    },
    "persona_expression": {
        "name": "人格表达",
        "description": "Agent 结果改写、进化 diff、宪法检查等需要理解人格的任务",
        "requires_tools": "false",
    },
    "lightweight_judgment": {
        "name": "轻量判断",
        "description": "消息分类、群聊参与判断、Skill 匹配等简单决策",
        "requires_tools": "false",
    },
    "memory_processing": {
        "name": "记忆处理",
        "description": "对话压缩、事实提取、兴趣追踪、自动记忆提取",
        "requires_tools": "false",
    },
    "self_reflection": {
        "name": "自省与进化",
        "description": "Lapwing 每日自省日志，调用频率低",
        "requires_tools": "false",
    },
    "agent_execution": {
        "name": "Agent 执行",
        "description": "子 Agent (Researcher/Coder/Browser 等) 的 LLM 调用，需要 tool calling",
        "requires_tools": "true",
    },
    "agent_coder": {
        "name": "Coder Agent",
        "description": "代码生成与执行 Agent，需要 tool calling",
        "requires_tools": "true",
    },
    "agent_team_lead": {
        "name": "TeamLead Agent",
        "description": "Agent Team 编排调度，需要 tool calling",
        "requires_tools": "true",
    },
    "agent_researcher": {
        "name": "Researcher Agent",
        "description": "搜索信息综合 Agent，需要 tool calling",
        "requires_tools": "true",
    },
    "heartbeat_proactive": {
        "name": "Heartbeat 主动",
        "description": "主动消息、兴趣分享、自主浏览等 heartbeat 行为",
        "requires_tools": "false",
    },
    "browser_vision": {
        "name": "浏览器视觉",
        "description": "浏览器页面截图的视觉理解，需要视觉能力的模型",
        "requires_tools": "false",
    },
}


@dataclass
class ModelInfo:
    id: str
    name: str


@dataclass
class ProviderInfo:
    id: str
    name: str
    api_type: str   # "openai" | "anthropic" | "codex_oauth"
    base_url: str
    api_key: str
    models: list[ModelInfo] = field(default_factory=list)
    reasoning_effort: str | None = None   # codex: "low" | "medium" | "high" | "xhigh"
    context_compaction: bool = False       # codex: server-side context compaction


@dataclass
class SlotAssignment:
    provider_id: str
    model_id: str
    fallback_model_ids: list[str] = field(default_factory=list)


@dataclass
class ModelRoutingConfig:
    providers: list[ProviderInfo] = field(default_factory=list)
    slots: dict[str, SlotAssignment] = field(default_factory=dict)


def _serialize(config: ModelRoutingConfig, *, include_api_key: bool = False) -> dict[str, Any]:
    """序列化为可写入 JSON 的 dict。

    include_api_key=False（默认）时 api_key 写为 "FROM_ENV"，
    避免明文密钥落盘。内部读取走内存中的 ProviderInfo.api_key。
    """
    return {
        "providers": [
            {
                "id": p.id,
                "name": p.name,
                "api_type": p.api_type,
                "base_url": p.base_url,
                "api_key": p.api_key if include_api_key else "FROM_ENV",
                "models": [{"id": m.id, "name": m.name} for m in p.models],
                **({"reasoning_effort": p.reasoning_effort} if p.reasoning_effort else {}),
                **({"context_compaction": True} if p.context_compaction else {}),
            }
            for p in config.providers
        ],
        "slots": {
            slot_id: {
                "provider_id": a.provider_id,
                "model_id": a.model_id,
                **({"fallback_model_ids": a.fallback_model_ids} if a.fallback_model_ids else {}),
            }
            for slot_id, a in config.slots.items()
        },
    }


def _deserialize(data: dict[str, Any]) -> ModelRoutingConfig:
    """从 JSON dict 反序列化。"""
    providers = []
    for p in data.get("providers", []):
        models = [ModelInfo(id=m["id"], name=m.get("name", m["id"]))
                  for m in p.get("models", [])]
        providers.append(ProviderInfo(
            id=p["id"],
            name=p.get("name", p["id"]),
            api_type=p.get("api_type", "openai"),
            base_url=p["base_url"],
            api_key=p.get("api_key", ""),
            models=models,
            reasoning_effort=p.get("reasoning_effort"),
            context_compaction=bool(p.get("context_compaction", False)),
        ))

    slots = {}
    for slot_id, assignment in data.get("slots", {}).items():
        if slot_id in SLOT_DEFINITIONS and assignment.get("provider_id"):
            slots[slot_id] = SlotAssignment(
                provider_id=assignment["provider_id"],
                model_id=assignment["model_id"],
                fallback_model_ids=assignment.get("fallback_model_ids", []),
            )

    return ModelRoutingConfig(providers=providers, slots=slots)


class ModelConfigManager:
    """模型路由配置管理器。

    提供 CRUD 操作，所有改动立即持久化。
    LLMRouter 持有一个引用，通过 resolve_slot() 获取实际的 base_url/model/api_key。
    """

    def __init__(self) -> None:
        self._config: ModelRoutingConfig = self._load_or_migrate()

    # ── 读取 ──

    def get_config(self) -> dict[str, Any]:
        """返回完整配置（给前端用）。api_key 脱敏。"""
        data = _serialize(self._config)
        # 脱敏 api_key
        for p in data["providers"]:
            key = p.get("api_key", "")
            if len(key) > 8:
                p["api_key_preview"] = key[:4] + "***" + key[-4:]
            else:
                p["api_key_preview"] = "***"
            del p["api_key"]
        # 附上 slot 定义
        data["slot_definitions"] = SLOT_DEFINITIONS
        return data

    def get_full_config(self) -> ModelRoutingConfig:
        """返回完整配置（内部用，含 api_key）。"""
        return self._config

    def resolve_slot(self, slot_id: str) -> tuple[str, str, str, str] | None:
        """解析 slot 到实际的 (base_url, model, api_key, api_type)。

        LLMRouter 调用此方法获取每个 slot 的路由信息。
        返回 None 表示 slot 未配置。
        """
        assignment = self._config.slots.get(slot_id)
        if assignment is None:
            return None

        provider = self._find_provider(assignment.provider_id)
        if provider is None:
            logger.warning(
                f"Slot '{slot_id}' references unknown provider "
                f"'{assignment.provider_id}'"
            )
            return None

        # 验证 model 在 provider 的 models 列表中
        model_ids = {m.id for m in provider.models}
        if assignment.model_id not in model_ids:
            logger.warning(
                f"Slot '{slot_id}' references unknown model "
                f"'{assignment.model_id}' in provider '{provider.id}'"
            )
            return None

        return (provider.base_url, assignment.model_id, provider.api_key, provider.api_type)

    def resolve_fallback_models(self, slot_id: str) -> list[str]:
        """返回 slot 的 fallback model 列表（同 provider 内的模型降级链）。"""
        assignment = self._config.slots.get(slot_id)
        if assignment is None:
            return []
        return list(assignment.fallback_model_ids)

    # ── Provider CRUD ──

    def add_provider(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str,
        api_type: str = "openai",
        models: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """添加一个 provider。"""
        if self._find_provider(provider_id) is not None:
            raise ValueError(f"Provider '{provider_id}' 已存在")

        model_list = [
            ModelInfo(id=m["id"], name=m.get("name", m["id"]))
            for m in (models or [])
        ]
        provider = ProviderInfo(
            id=provider_id,
            name=name,
            api_type=api_type,
            base_url=base_url,
            api_key=api_key,
            models=model_list,
        )
        self._config.providers.append(provider)
        self._save()
        return {"status": "ok", "provider_id": provider_id}

    def update_provider(
        self,
        provider_id: str,
        **updates,
    ) -> dict[str, Any]:
        """更新 provider 的字段。"""
        provider = self._find_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider '{provider_id}' 不存在")

        if "name" in updates:
            provider.name = updates["name"]
        if "base_url" in updates:
            provider.base_url = updates["base_url"]
        if "api_key" in updates:
            provider.api_key = updates["api_key"]
        if "api_type" in updates:
            provider.api_type = updates["api_type"]
        if "models" in updates:
            provider.models = [
                ModelInfo(id=m["id"], name=m.get("name", m["id"]))
                for m in updates["models"]
            ]
            # 如果删了某个 model，检查是否有 slot 在用它
            current_model_ids = {m.id for m in provider.models}
            for slot_id, assignment in self._config.slots.items():
                if (assignment.provider_id == provider_id
                        and assignment.model_id not in current_model_ids):
                    logger.warning(
                        f"Slot '{slot_id}' 使用的模型 "
                        f"'{assignment.model_id}' 已从 provider 中移除"
                    )

        self._save()
        return {"status": "ok"}

    def remove_provider(self, provider_id: str) -> dict[str, Any]:
        """删除 provider。如果有 slot 在用，拒绝删除。"""
        using_slots = [
            slot_id for slot_id, a in self._config.slots.items()
            if a.provider_id == provider_id
        ]
        if using_slots:
            raise ValueError(
                f"无法删除 provider '{provider_id}'，"
                f"以下 slot 正在使用: {', '.join(using_slots)}"
            )

        self._config.providers = [
            p for p in self._config.providers if p.id != provider_id
        ]
        self._save()
        return {"status": "ok"}

    # ── Slot 分配 ──

    def assign_slot(
        self,
        slot_id: str,
        provider_id: str,
        model_id: str,
    ) -> dict[str, Any]:
        """给一个 slot 分配模型。"""
        if slot_id not in SLOT_DEFINITIONS:
            raise ValueError(f"未知 slot: '{slot_id}'")

        provider = self._find_provider(provider_id)
        if provider is None:
            raise ValueError(f"Provider '{provider_id}' 不存在")

        model_ids = {m.id for m in provider.models}
        if model_id not in model_ids:
            raise ValueError(
                f"模型 '{model_id}' 不在 provider '{provider_id}' 的模型列表中"
            )

        self._config.slots[slot_id] = SlotAssignment(
            provider_id=provider_id,
            model_id=model_id,
        )
        self._save()
        return {"status": "ok", "slot_id": slot_id}

    # ── 内部方法 ──

    def _find_provider(self, provider_id: str) -> ProviderInfo | None:
        for p in self._config.providers:
            if p.id == provider_id:
                return p
        return None

    def _save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(_serialize(self._config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Model routing config saved")

    def _load_or_migrate(self) -> ModelRoutingConfig:
        """加载配置文件。不存在时从 .env 迁移。"""
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                config = _deserialize(data)
                self._resolve_env_keys(config)
                logger.info(
                    f"Loaded model config: "
                    f"{len(config.providers)} providers, "
                    f"{len(config.slots)} slots assigned"
                )
                return config
            except Exception as e:
                logger.error(f"Failed to load model config: {e}")

        # 从 .env 迁移
        return self._migrate_from_env()

    @staticmethod
    def _resolve_env_keys(config: ModelRoutingConfig) -> None:
        """将 api_key 为 'FROM_ENV' 或空的 provider 从环境变量回填。"""
        import os
        from config.settings import LLM_API_KEY, NIM_API_KEY
        for p in config.providers:
            if p.api_key and p.api_key != "FROM_ENV":
                continue
            # 按 provider id / base_url 推断对应的环境变量
            # 直接读 os.getenv：运行时热切换 model 时 key 可能已被外部更新，需要最新值
            if "nvidia" in p.base_url.lower() or p.id == "nvidia":
                p.api_key = NIM_API_KEY or os.getenv("NIM_API_KEY", "")
            else:
                p.api_key = LLM_API_KEY or os.getenv("LLM_API_KEY", "")

    def _migrate_from_env(self) -> ModelRoutingConfig:
        """从现有 .env 配置迁移生成初始 model_routing.json。"""
        from config.settings import (
            LLM_BASE_URL, LLM_MODEL, LLM_API_KEY,
            LLM_CHAT_BASE_URL, LLM_CHAT_MODEL, LLM_CHAT_API_KEY,
            LLM_TOOL_BASE_URL, LLM_TOOL_MODEL, LLM_TOOL_API_KEY,
            NIM_BASE_URL, NIM_MODEL, NIM_API_KEY,
        )

        providers: dict[str, ProviderInfo] = {}
        slot_map: dict[str, SlotAssignment] = {}

        # 收集所有不同的 base_url → provider
        configs = [
            ("default", LLM_BASE_URL, LLM_MODEL, LLM_API_KEY),
            ("chat", LLM_CHAT_BASE_URL or LLM_BASE_URL,
             LLM_CHAT_MODEL or LLM_MODEL,
             LLM_CHAT_API_KEY or LLM_API_KEY),
            ("tool", LLM_TOOL_BASE_URL or LLM_BASE_URL,
             LLM_TOOL_MODEL or LLM_MODEL,
             LLM_TOOL_API_KEY or LLM_API_KEY),
            ("heartbeat", NIM_BASE_URL or LLM_BASE_URL,
             NIM_MODEL or LLM_MODEL,
             NIM_API_KEY or LLM_API_KEY),
        ]

        provider_by_url: dict[str, str] = {}
        counter = 0

        for label, base_url, model, api_key in configs:
            if not base_url:
                continue

            if base_url not in provider_by_url:
                counter += 1
                pid = f"provider_{counter}"
                pname = f"Provider {counter}"
                # 尝试从 URL 推断名称
                if "minimax" in base_url.lower():
                    pid = "minimax"
                    pname = "MiniMax"
                elif "volces.com" in base_url.lower() or "volcengine" in base_url.lower():
                    pid = "volcengine"
                    pname = "火山方舟"
                elif "bigmodel" in base_url.lower():
                    pid = "glm"
                    pname = "GLM (智谱)"
                elif "nvidia" in base_url.lower():
                    pid = "nvidia"
                    pname = "NVIDIA NIM"
                elif "anthropic" in base_url.lower():
                    pid = "anthropic"
                    pname = "Anthropic"

                from src.core.llm_protocols import _detect_api_type
                providers[pid] = ProviderInfo(
                    id=pid,
                    name=pname,
                    api_type=_detect_api_type(base_url),
                    base_url=base_url,
                    api_key=api_key,
                    models=[],
                )
                provider_by_url[base_url] = pid

            pid = provider_by_url[base_url]
            # 添加 model（去重）
            existing_model_ids = {m.id for m in providers[pid].models}
            if model and model not in existing_model_ids:
                providers[pid].models.append(ModelInfo(id=model, name=model))

        # 映射旧 purpose → 新 slot
        chat_url = LLM_CHAT_BASE_URL or LLM_BASE_URL
        chat_model = LLM_CHAT_MODEL or LLM_MODEL
        tool_url = LLM_TOOL_BASE_URL or LLM_BASE_URL
        tool_model = LLM_TOOL_MODEL or LLM_MODEL
        hb_url = NIM_BASE_URL or LLM_BASE_URL
        hb_model = NIM_MODEL or LLM_MODEL

        def _make_assignment(url: str, model: str) -> SlotAssignment | None:
            pid = provider_by_url.get(url)
            if pid and model:
                return SlotAssignment(provider_id=pid, model_id=model)
            return None

        # 旧 purpose=chat 的 slot
        chat_assign = _make_assignment(chat_url, chat_model)
        if chat_assign:
            slot_map["main_conversation"] = chat_assign
            slot_map["persona_expression"] = chat_assign
            slot_map["self_reflection"] = chat_assign

        # 旧 purpose=tool 的 slot
        tool_assign = _make_assignment(tool_url, tool_model)
        if tool_assign:
            slot_map["lightweight_judgment"] = tool_assign
            slot_map["memory_processing"] = tool_assign
            slot_map["agent_execution"] = tool_assign

        # 旧 purpose=heartbeat 的 slot
        hb_assign = _make_assignment(hb_url, hb_model)
        if hb_assign:
            slot_map["heartbeat_proactive"] = hb_assign

        config = ModelRoutingConfig(
            providers=list(providers.values()),
            slots=slot_map,
        )

        # 保存
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(_serialize(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            f"Migrated .env config to model_routing.json: "
            f"{len(providers)} providers, {len(slot_map)} slots"
        )
        return config
