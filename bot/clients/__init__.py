from bot.clients.base import DownloadClientBase
from bot.clients.download_station import DownloadStationClient
from bot.clients.qbittorrent import QBittorrentClient
from bot.clients.transmission import TransmissionClient


def create_download_client(config) -> DownloadClientBase:
    """工厂函数：根据配置创建下载客户端"""
    client_type = config.client_type.lower()

    if client_type == "download_station":
        return DownloadStationClient(
            host=config.ds_host,
            username=config.ds_username,
            password=config.ds_password,
        )
    elif client_type == "qbittorrent":
        return QBittorrentClient(
            host=config.qb_host,
            username=config.qb_username,
            password=config.qb_password,
        )
    elif client_type == "transmission":
        return TransmissionClient(
            host=config.tr_host,
            username=config.tr_username,
            password=config.tr_password,
        )
    else:
        raise ValueError(f"不支持的下载客户端类型: {client_type}")


__all__ = [
    "DownloadClientBase",
    "DownloadStationClient",
    "QBittorrentClient",
    "TransmissionClient",
    "create_download_client",
]
