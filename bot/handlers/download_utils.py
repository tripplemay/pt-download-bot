"""PT 种子下载的共享处理逻辑。"""

from __future__ import annotations

from typing import Optional

from bot.pt.base import TorrentResult


async def add_pt_torrent_file(
    pt_client,
    dl_client,
    selected: TorrentResult,
    *,
    cookie: str = "",
) -> Optional[str]:
    """由机器人下载 PT 种子文件后上传，避免下载器被重定向到登录页。"""
    return await add_torrent_url_as_file(
        pt_client,
        dl_client,
        selected.torrent_url,
        f"{selected.title[:80]}.torrent",
        cookie=cookie,
    )


async def add_torrent_url_as_file(
    pt_client,
    dl_client,
    torrent_url: str,
    filename: str,
    *,
    cookie: str = "",
) -> Optional[str]:
    """Fetch an authenticated torrent URL in the Bot and upload its bytes."""
    torrent_bytes = await pt_client.download_torrent(
        torrent_url,
        cookie=cookie,
    )
    return await dl_client.add_torrent_file(
        torrent_bytes,
        filename,
    )
