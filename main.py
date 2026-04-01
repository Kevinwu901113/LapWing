"""Lapwing 启动入口（薄适配层 + auth CLI）。"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Any

from config.settings import (
    DB_PATH,
    DATA_DIR,
    LOG_LEVEL,
    LOGS_DIR,
    TELEGRAM_PROXY_URL,
    TELEGRAM_TOKEN,
)
from src.auth.service import AuthManager


def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    level = getattr(logging, LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Lapwing logger (project code) ──
    lapwing_logger = logging.getLogger("lapwing")
    lapwing_logger.setLevel(level)
    lapwing_logger.propagate = False  # never propagate to root

    if not lapwing_logger.handlers:
        # Main log: rotated, 10MB per file, keep 5 backups
        main_fh = RotatingFileHandler(
            LOGS_DIR / "lapwing.log",
            encoding="utf-8",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        main_fh.setFormatter(fmt)
        main_fh.setLevel(level)

        # Console: same level
        console_sh = logging.StreamHandler()
        console_sh.setFormatter(fmt)
        console_sh.setLevel(level)

        lapwing_logger.addHandler(main_fh)
        lapwing_logger.addHandler(console_sh)

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


def run_telegram_bot(logger: logging.Logger) -> int:
    from src.app.container import AppContainer
    from src.app.telegram_app import TelegramApp
    from config.settings import TELEGRAM_KEVIN_ID

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
        return 1

    logger.info("Lapwing 正在启动...")

    # 生成/更新 vital manifest（供 Sentinel 哨兵使用）
    try:
        from src.core.vital_guard import save_manifest
        save_manifest()
        logger.info("Vital manifest 已更新。")
    except Exception as _manifest_err:
        logger.warning("Vital manifest 生成失败: %s", _manifest_err)

    container = AppContainer(db_path=DB_PATH, data_dir=DATA_DIR)

    telegram_app = TelegramApp(container=container, tg_config={"kevin_id": TELEGRAM_KEVIN_ID})
    container.telegram_app = telegram_app

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

        async def _qq_on_message(chat_id: str, text: str, channel, raw_event: dict) -> None:
            """QQ 消息进入 Brain 的桥接。"""
            container.channel_manager.last_active_channel = channel
            brain = container.brain

            async def send_fn(reply_text: str) -> None:
                await container.channel_manager.send(ChannelType.QQ, chat_id, reply_text)

            async def typing_fn() -> None:
                pass  # QQ 无 typing indicator

            async def noop_status(cid: str, t: str) -> None:
                pass

            await brain.think_conversational(
                chat_id,
                text,
                send_fn=send_fn,
                typing_fn=typing_fn,
                status_callback=noop_status,
                adapter="qq",
                user_id=str(raw_event.get("user_id", "")),
            )

        qq_adapter = QQAdapter(config=qq_config, on_message=_qq_on_message)
        qq_adapter.router = container.brain.router  # Inject LLM router for group decisions
        if qq_adapter._decider is not None:
            qq_adapter._decider.set_router(container.brain.router)
        container.channel_manager.register(ChannelType.QQ, qq_adapter)
        logger.info("QQ 通道已注册（群聊: %s）", QQ_GROUP_IDS or "无")

    app = telegram_app.build_application(
        token=TELEGRAM_TOKEN,
        proxy_url=TELEGRAM_PROXY_URL,
    )
    app.run_polling(drop_pending_updates=True)
    logger.info("Lapwing 已关闭")
    return 0


def main() -> None:
    logger = setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.top_command == "auth":
            raise SystemExit(handle_auth_command(args))
        raise SystemExit(run_telegram_bot(logger))
    except KeyboardInterrupt:
        logger.info("Lapwing 已取消")
        raise SystemExit(130)
    except Exception as exc:
        logger.error("执行失败: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
