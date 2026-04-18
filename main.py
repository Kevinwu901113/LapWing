"""Lapwing 启动入口（薄适配层 + auth CLI）。"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any

from config.settings import (
    DB_PATH,
    DATA_DIR,
    LOG_LEVEL,
    LOGS_DIR,
)
from src.auth.service import AuthManager

_PID_FILE = None  # 模块级引用，防止 GC 释放文件描述符


def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    level = getattr(logging, LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Lapwing logger (project code) ──
    lapwing_logger = logging.getLogger("lapwing")
    lapwing_logger.handlers.clear()
    lapwing_logger.setLevel(level)
    lapwing_logger.propagate = False  # never propagate to root

    if not lapwing_logger.handlers:
        # Main log: rotated, 5MB per file, keep 2 backups (reduced — durable records live in StateMutationLog)
        main_fh = RotatingFileHandler(
            LOGS_DIR / "lapwing.log",
            encoding="utf-8",
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
        )
        main_fh.setFormatter(fmt)
        main_fh.setLevel(level)

        # Console: same level
        console_sh = logging.StreamHandler()
        console_sh.setFormatter(fmt)
        console_sh.setLevel(level)

        lapwing_logger.addHandler(main_fh)
        lapwing_logger.addHandler(console_sh)

    # ── 业务信息由 StateMutationLog 负责，内部模块降噪到 WARNING ──
    for module_name in (
        "lapwing.core.brain",
        "lapwing.core.task_runtime",
        "lapwing.core.llm_router",
        "lapwing.core.llm_protocols",
        "lapwing.core.prompt_builder",
        "lapwing.core.heartbeat",
        "lapwing.core.consciousness",
        "lapwing.memory",
        "lapwing.tools",
        "lapwing.core.channel_manager",
    ):
        logging.getLogger(module_name).setLevel(logging.WARNING)

    # 保持 INFO 的模块（启动/关闭等关键流程）
    logging.getLogger("lapwing.app.container").setLevel(logging.INFO)
    logging.getLogger("lapwing.event_logger").setLevel(logging.INFO)

    # ── Root logger (third-party libraries) ──
    # Separate handlers — never share with lapwing logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)  # only warnings+ from third-party

    if not root_logger.handlers:
        lib_fh = RotatingFileHandler(
            LOGS_DIR / "libraries.log",
            encoding="utf-8",
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
        )
        lib_fh.setFormatter(fmt)
        lib_fh.setLevel(logging.WARNING)
        root_logger.addHandler(lib_fh)

    # ── Quiet down noisy libraries ──
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return lapwing_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lapwing CLI")
    subparsers = parser.add_subparsers(dest="top_command")

    auth_parser = subparsers.add_parser("auth", help="管理上游 provider auth")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)

    list_parser = auth_subparsers.add_parser("list", help="列出 auth profiles")
    list_parser.add_argument("--provider", default=None, help="按 provider 过滤，如 openai")

    login_parser = auth_subparsers.add_parser("login", help="执行 OAuth 登录")
    login_subparsers = login_parser.add_subparsers(dest="login_provider", required=True)
    openai_login_parser = login_subparsers.add_parser(
        "openai-codex",
        help="通过 OpenAI/Codex OAuth (PKCE) 登录",
    )
    openai_login_parser.add_argument("--profile-id", default=None)
    openai_login_parser.add_argument("--no-browser", action="store_true")

    import_parser = auth_subparsers.add_parser(
        "import",
        help="导入现有 auth cache",
    )
    import_subparsers = import_parser.add_subparsers(dest="import_source", required=True)
    codex_import_parser = import_subparsers.add_parser(
        "codex-auth-json",
        help="导入 ~/.codex/auth.json",
    )
    codex_import_parser.add_argument("--path", default="~/.codex/auth.json")
    codex_import_parser.add_argument("--profile-id", default=None)

    set_api_key_parser = auth_subparsers.add_parser(
        "set-api-key",
        help="保存 API key profile",
    )
    set_api_key_parser.add_argument("--provider", required=True)
    set_api_key_parser.add_argument("--profile-id", default=None)
    secret_group = set_api_key_parser.add_mutually_exclusive_group(required=True)
    secret_group.add_argument("--literal", default=None)
    secret_group.add_argument("--env", dest="env_name", default=None)
    secret_group.add_argument("--command", dest="command_value", default=None)

    bind_parser = auth_subparsers.add_parser("bind", help="绑定 purpose 到指定 profile")
    bind_parser.add_argument(
        "--purpose",
        required=True,
        choices=("default", "chat", "tool", "heartbeat"),
    )
    bind_parser.add_argument(
        "--profile",
        required=True,
        help="profile id，例如 openai:default 或 openai:user@example.com",
    )

    unbind_parser = auth_subparsers.add_parser("unbind", help="取消 purpose 的显式绑定")
    unbind_parser.add_argument(
        "--purpose",
        required=True,
        choices=("default", "chat", "tool", "heartbeat"),
    )

    # ── credential 子命令 ─────────────────────────────────────────────────────
    cred_parser = subparsers.add_parser("credential", help="管理加密凭据保险库")
    cred_subparsers = cred_parser.add_subparsers(dest="cred_command", required=True)

    cred_subparsers.add_parser("list", help="列出已存储的服务名称")

    cred_set_parser = cred_subparsers.add_parser("set", help="设置服务凭据")
    cred_set_parser.add_argument("service", help="服务名称，如 github")
    cred_set_parser.add_argument("--username", required=True, help="用户名或邮箱")
    cred_set_parser.add_argument("--login-url", required=True, help="登录页 URL")

    cred_del_parser = cred_subparsers.add_parser("delete", help="删除服务凭据")
    cred_del_parser.add_argument("service", help="服务名称")

    cred_subparsers.add_parser("generate-key", help="生成新的 Fernet 加密密钥")

    return parser


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def handle_auth_command(args: argparse.Namespace) -> int:
    auth = AuthManager()

    if args.auth_command == "list":
        _print_json(auth.list_profiles(provider=args.provider))
        return 0

    if args.auth_command == "login" and args.login_provider == "openai-codex":
        profile_id, profile = auth.login_oauth(
            provider="openai",
            method="pkce",
            profile_id=args.profile_id,
            no_browser=args.no_browser,
        )
        _print_json({"success": True, "profileId": profile_id, "profile": profile})
        return 0

    if args.auth_command == "import" and args.import_source == "codex-auth-json":
        profile_id, profile = auth.import_codex_auth_json(
            path=args.path,
            profile_id=args.profile_id,
        )
        _print_json({"success": True, "profileId": profile_id, "profile": profile})
        return 0

    if args.auth_command == "set-api-key":
        profile_id, profile = auth.set_api_key(
            provider=args.provider,
            profile_id=args.profile_id,
            literal=args.literal,
            env_name=args.env_name,
            command=args.command_value,
        )
        _print_json({"success": True, "profileId": profile_id, "profile": profile})
        return 0

    if args.auth_command == "bind":
        profile_id = auth.bind_profile(purpose=args.purpose, profile_id=args.profile)
        _print_json({"success": True, "purpose": args.purpose, "profileId": profile_id})
        return 0

    if args.auth_command == "unbind":
        cleared = auth.unbind_profile(purpose=args.purpose)
        _print_json({"success": True, "purpose": args.purpose, "cleared": cleared})
        return 0

    raise ValueError("未知 auth 子命令")


def handle_credential_command(args: argparse.Namespace) -> int:
    if args.cred_command == "generate-key":
        from src.core.credential_vault import CredentialVault
        print(CredentialVault.generate_key())
        return 0

    # list / set / delete 都需要实例化 vault
    from src.core.credential_vault import CredentialVault
    vault = CredentialVault()

    if args.cred_command == "list":
        services = vault.list_services()
        _print_json(services)
        return 0

    if args.cred_command == "set":
        import getpass
        from src.core.credential_vault import Credential
        password = getpass.getpass("Password: ")
        cred = Credential(
            service=args.service,
            username=args.username,
            password=password,
            login_url=args.login_url,
        )
        vault.set(args.service, cred)
        _print_json({"success": True, "service": args.service})
        return 0

    if args.cred_command == "delete":
        deleted = vault.delete(args.service)
        _print_json({"success": deleted, "service": args.service})
        return 0

    raise ValueError("未知 credential 子命令")


def run_bot(logger: logging.Logger) -> int:
    import asyncio
    import signal

    global _PID_FILE
    pid_path = DATA_DIR / "lapwing.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    _PID_FILE = open(pid_path, "w")
    try:
        fcntl.flock(_PID_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _PID_FILE.write(str(os.getpid()))
        _PID_FILE.flush()
    except BlockingIOError:
        logger.error("另一个 Lapwing 进程正在运行（PID 文件锁定）。请先停止旧进程。")
        return 1

    from src.app.container import AppContainer

    logger.info("Lapwing 正在启动...")

    # 生成/更新 vital manifest（供 Sentinel 哨兵使用）
    try:
        from src.core.vital_guard import save_manifest
        save_manifest()
        logger.info("Vital manifest 已更新。")
    except Exception as _manifest_err:
        logger.warning("Vital manifest 生成失败: %s", _manifest_err)

    container = AppContainer(db_path=DB_PATH, data_dir=DATA_DIR)

    # Register QQ adapter if enabled
    from config.settings import QQ_ENABLED
    if QQ_ENABLED:
        from config.settings import (
            QQ_WS_URL, QQ_ACCESS_TOKEN, QQ_SELF_ID, QQ_KEVIN_ID,
            QQ_GROUP_IDS, QQ_GROUP_CONTEXT_SIZE, QQ_GROUP_COOLDOWN,
            QQ_GROUP_INTEREST_KEYWORDS,
        )
        from src.adapters.base import ChannelType
        from src.adapters.qq_adapter import QQAdapter

        qq_config = {
            "ws_url": QQ_WS_URL,
            "access_token": QQ_ACCESS_TOKEN,
            "self_id": QQ_SELF_ID,
            "kevin_id": QQ_KEVIN_ID,
            "group_ids": QQ_GROUP_IDS,
            "self_names": ["Lapwing", "lapwing", "小翅"],
            "interest_keywords": QQ_GROUP_INTEREST_KEYWORDS,
            "group_cooldown": QQ_GROUP_COOLDOWN,
            "group_context_size": QQ_GROUP_CONTEXT_SIZE,
        }

        async def _qq_cmd_model(chat_id: str, text: str, brain, send_fn) -> None:
            """处理 QQ 的 /model 命令。"""
            parts = text.strip().split(None, 1)
            args = parts[1].strip() if len(parts) > 1 else ""
            keyword = args.lower().split()[0] if args else ""

            try:
                if not args or keyword == "list":
                    options = brain.list_model_options()
                    status = brain.model_status(chat_id)
                    lines = ["可用模型："]
                    for o in options:
                        alias = str(o.get("alias") or "").strip()
                        ref = str(o.get("ref") or "").strip()
                        idx = o.get("index")
                        lines.append(f"{idx}. {alias + ' -> ' + ref if alias else ref}")
                    purposes = dict(status.get("purposes", {}) or {})
                    if purposes:
                        lines.append("")
                        for p in ("chat", "tool", "heartbeat"):
                            d = purposes.get(p) or {}
                            eff = str(d.get("effective") or "").strip()
                            if eff:
                                suffix = " (override)" if d.get("override") else ""
                                lines.append(f"- {p}: {eff}{suffix}")
                    await send_fn("\n".join(lines))
                elif keyword == "status":
                    status = brain.model_status(chat_id)
                    purposes = dict(status.get("purposes", {}) or {})
                    lines = ["模型状态："]
                    for p in ("chat", "tool", "heartbeat"):
                        d = purposes.get(p) or {}
                        lines.append(f"- {p}: {d.get('effective', '?')}")
                    await send_fn("\n".join(lines))
                elif keyword == "default":
                    result = brain.reset_model(chat_id)
                    await send_fn(f"已恢复默认模型（清除 {result.get('cleared', 0)} 个覆盖）。")
                else:
                    # "switch" 是可选关键词，跳过
                    selector = args
                    if keyword == "switch":
                        selector = args.split(None, 1)[1] if " " in args else ""
                    result = brain.switch_model(chat_id, selector)
                    selected = dict(result.get("selected", {}) or {})
                    await send_fn(f"已切换：{selected.get('ref', args)}")
            except ValueError as exc:
                await send_fn(f"切换失败：{exc}")
            except Exception as exc:
                logger.warning("[qq/model] %s", exc)
                await send_fn("模型切换失败，请稍后再试。")

        async def _qq_on_message(
            chat_id: str, text: str, channel, raw_event: dict,
            image_urls: list[str] | None = None,
        ) -> None:
            """QQ 消息进入 Brain 的桥接。"""
            container.channel_manager.last_active_channel = channel
            brain = container.brain

            async def send_fn(reply_text: str) -> None:
                await container.channel_manager.send(ChannelType.QQ, chat_id, reply_text)

            # 命令拦截
            if text.startswith("/model") or text.startswith("/models"):
                await _qq_cmd_model(chat_id, text, brain, send_fn)
                return

            # 图片下载转 base64（QQ CDN URL 有时效性，需在调用 LLM 前下载）
            images: list[dict] | None = None
            if image_urls:
                qq_adp = container.channel_manager.adapters.get(ChannelType.QQ)
                if qq_adp is not None:
                    downloaded = []
                    for url in image_urls:
                        img = await qq_adp._download_image_as_base64(url)
                        if img:
                            downloaded.append(img)
                    if downloaded:
                        images = downloaded

            async def typing_fn() -> None:
                pass  # QQ 无 typing indicator

            async def noop_status(cid: str, t: str) -> None:
                pass

            # v2.0 Step 4: route through MainLoop's EventQueue instead
            # of calling brain directly. Adapters are now event producers,
            # MainLoop is the sole consumer.
            from src.core.authority_gate import AuthLevel, identify
            from src.core.events import MessageEvent

            user_id = str(raw_event.get("user_id", ""))
            auth_level = identify("qq", user_id)
            event = MessageEvent.from_message(
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                adapter="qq",
                send_fn=send_fn,
                auth_level=int(auth_level),
                images=tuple(images) if images else (),
                typing_fn=typing_fn,
                status_callback=noop_status,
            )
            await container.event_queue.put(event)

        qq_adapter = QQAdapter(config=qq_config, on_message=_qq_on_message)
        qq_adapter.router = container.brain.router  # Inject LLM router for group decisions
        if qq_adapter._decider is not None:
            qq_adapter._decider.set_router(container.brain.router)
        container.channel_manager.register(ChannelType.QQ, qq_adapter)
        logger.info("QQ 通道已注册（群聊: %s）", QQ_GROUP_IDS or "无")

    async def _run() -> None:
        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        async def _send_to_owner(text: str) -> None:
            await container.channel_manager.send_to_owner(text)

        await container.start(send_fn=_send_to_owner)
        logger.info("Lapwing 已启动，等待消息...")
        await stop_event.wait()
        logger.info("收到停止信号，正在关闭...")
        await container.shutdown()

    asyncio.run(_run())
    logger.info("Lapwing 已关闭")
    return 0


def main() -> None:
    logger = setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.top_command == "auth":
            raise SystemExit(handle_auth_command(args))
        if args.top_command == "credential":
            raise SystemExit(handle_credential_command(args))
        raise SystemExit(run_bot(logger))
    except KeyboardInterrupt:
        logger.info("Lapwing 已取消")
        raise SystemExit(130)
    except Exception as exc:
        logger.error("执行失败: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
