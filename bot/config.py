"""环境变量加载 — 只加载启动必需的 2 个环境变量"""

import os


def load_config() -> tuple:
    """加载启动必需的环境变量，返回 (bot_token, owner_id)。

    其他配置（PT 站、下载客户端等）改为从数据库读取，
    通过 Bot 命令或首次启动时从 .env 迁移。
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_id_str = os.environ.get("OWNER_TELEGRAM_ID", "")

    if not bot_token:
        raise ValueError("缺少环境变量 TELEGRAM_BOT_TOKEN")
    if not owner_id_str:
        raise ValueError("缺少环境变量 OWNER_TELEGRAM_ID")

    return bot_token, int(owner_id_str)
