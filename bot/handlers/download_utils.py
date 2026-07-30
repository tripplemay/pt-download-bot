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
    torrent_bytes = await pt_client.download_torrent(
        selected.torrent_url,
        cookie=cookie,
    )
    return await dl_client.add_torrent_file(
        torrent_bytes,
        f"{selected.title[:80]}.torrent",
    )
