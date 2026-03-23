"""环境变量加载 — 从 os.environ 读取配置"""

import os
from dataclasses import dataclass


@dataclass
class TelegramConfig:
    bot_token: str
    owner_id: int


@dataclass
class PTConfig:
    site_url: str
    passkey: str
    max_results: int = 50
    page_size: int = 10


@dataclass
class DownloadClientConfig:
    client_type: str
    ds_host: str = ""
    ds_username: str = ""
    ds_password: str = ""
    qb_host: str = ""
    qb_username: str = ""
    qb_password: str = ""
    tr_host: str = ""
    tr_username: str = ""
    tr_password: str = ""


def load_config() -> tuple[TelegramConfig, PTConfig, DownloadClientConfig]:
    """从 os.environ 加载所有配置，缺少必填项时抛出 ValueError。"""

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    owner_id_str = os.environ.get("OWNER_TELEGRAM_ID", "")

    if not bot_token:
        raise ValueError("缺少环境变量 TELEGRAM_BOT_TOKEN")
    if not owner_id_str:
        raise ValueError("缺少环境变量 OWNER_TELEGRAM_ID")

    telegram_cfg = TelegramConfig(
        bot_token=bot_token,
        owner_id=int(owner_id_str),
    )

    site_url = os.environ.get("PT_SITE_URL", "")
    passkey = os.environ.get("PT_PASSKEY", "")

    if not site_url:
        raise ValueError("缺少环境变量 PT_SITE_URL")
    if not passkey:
        raise ValueError("缺少环境变量 PT_PASSKEY")

    pt_cfg = PTConfig(
        site_url=site_url,
        passkey=passkey,
        max_results=int(os.environ.get("PT_MAX_RESULTS", "50")),
        page_size=int(os.environ.get("PT_PAGE_SIZE", "10")),
    )

    client_type = os.environ.get("DOWNLOAD_CLIENT", "download_station")

    dl_cfg = DownloadClientConfig(
        client_type=client_type,
        ds_host=os.environ.get("DS_HOST", ""),
        ds_username=os.environ.get("DS_USERNAME", ""),
        ds_password=os.environ.get("DS_PASSWORD", ""),
        qb_host=os.environ.get("QB_HOST", ""),
        qb_username=os.environ.get("QB_USERNAME", ""),
        qb_password=os.environ.get("QB_PASSWORD", ""),
        tr_host=os.environ.get("TR_HOST", ""),
        tr_username=os.environ.get("TR_USERNAME", ""),
        tr_password=os.environ.get("TR_PASSWORD", ""),
    )

    return telegram_cfg, pt_cfg, dl_cfg
