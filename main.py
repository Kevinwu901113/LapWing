"""Lapwing 启动入口（薄适配层）。"""

import logging

from config.settings import (
    DB_PATH,
    DATA_DIR,
    LOG_LEVEL,
    LOGS_DIR,
    TELEGRAM_PROXY_URL,
    TELEGRAM_TOKEN,
)
from src.app.container import AppContainer
from src.app.telegram_app import TelegramApp


def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / "lapwing.log", encoding="utf-8", mode="a"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("lapwing")


def main() -> None:
    logger = setup_logging()

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN 未配置！请检查 config/.env")
        raise SystemExit(1)

    logger.info("Lapwing 正在启动...")
    container = AppContainer(db_path=DB_PATH, data_dir=DATA_DIR)
    telegram_app = TelegramApp(container=container)
    container.telegram_app = telegram_app

    app = telegram_app.build_application(
        token=TELEGRAM_TOKEN,
        proxy_url=TELEGRAM_PROXY_URL,
    )
    app.run_polling(drop_pending_updates=True)
    logger.info("Lapwing 已关闭")


if __name__ == "__main__":
    main()
