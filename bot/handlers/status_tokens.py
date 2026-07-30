"""短生命周期的 Download Station 状态操作令牌。"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import secrets
import time
from typing import Any, Callable, Mapping


STATUS_TOKEN_REGISTRY_KEY = "status_token_registry"
DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_MAX_ENTRIES = 2048
TOKEN_BYTES = 9
CALLBACK_PREFIX = "dst"
TELEGRAM_CALLBACK_DATA_LIMIT = 64


@dataclass(frozen=True)
class StatusTokenEntry:
    """服务端保存的状态页操作上下文。"""

    viewer_id: int
    task_id: str
    binding_id: int | None
    mode: str
    view: str
    page: int
    action: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    expires_at: float = 0.0


class StatusTokenRegistry:
    """进程内、实例隔离的短令牌注册表。"""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self.ttl_seconds = float(ttl_seconds)
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, StatusTokenEntry] = OrderedDict()

    def create(
        self,
        *,
        viewer_id: int,
        task_id: str,
        binding_id: int | None,
        mode: str,
        view: str,
        page: int,
        action: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """保存操作上下文并返回 URL-safe 短令牌。"""
        now = self._clock()
        self.cleanup(now=now)
        while len(self._entries) >= self.max_entries:
            self._entries.popitem(last=False)

        token = self._new_token()
        self._entries[token] = StatusTokenEntry(
            viewer_id=viewer_id,
            task_id=str(task_id),
            binding_id=binding_id,
            mode=mode,
            view=view,
            page=page,
            action=action,
            metadata=dict(metadata or {}),
            expires_at=now + self.ttl_seconds,
        )
        return token

    def get(self, token: str) -> StatusTokenEntry | None:
        """读取有效记录，不消费令牌。"""
        self.cleanup()
        return self._entries.get(token)

    def consume(self, token: str) -> StatusTokenEntry | None:
        """原子移除并返回有效记录；令牌至多成功消费一次。"""
        now = self._clock()
        entry = self._entries.pop(token, None)
        self.cleanup(now=now)
        if entry is None or entry.expires_at <= now:
            return None
        return entry

    def cleanup(self, *, now: float | None = None) -> int:
        """移除全部过期记录，返回移除数量。"""
        if now is None:
            now = self._clock()
        expired = [
            token
            for token, entry in self._entries.items()
            if entry.expires_at <= now
        ]
        for token in expired:
            self._entries.pop(token, None)
        return len(expired)

    def __len__(self) -> int:
        return len(self._entries)

    def _new_token(self) -> str:
        while True:
            token = secrets.token_urlsafe(TOKEN_BYTES)
            if token not in self._entries:
                return token


def build_status_callback_data(operation: str, token: str) -> str:
    """构造 Telegram callback_data，并拒绝不可解析或过长的数据。"""
    if not operation or ":" in operation:
        raise ValueError("operation must be non-empty and cannot contain ':'")
    if not token or ":" in token:
        raise ValueError("token must be non-empty and cannot contain ':'")

    callback_data = f"{CALLBACK_PREFIX}:{operation}:{token}"
    if len(callback_data.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT:
        raise ValueError("callback_data exceeds Telegram's 64-byte limit")
    return callback_data
