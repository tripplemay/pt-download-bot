"""Tests for the status action token registry."""

import re

import pytest

from bot.handlers.status_tokens import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    STATUS_TOKEN_REGISTRY_KEY,
    StatusTokenRegistry,
    build_status_callback_data,
)


class MutableClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def _create(registry: StatusTokenRegistry, task_id: str = "task-1") -> str:
    return registry.create(
        viewer_id=123,
        task_id=task_id,
        binding_id=42,
        mode="mine",
        view="active",
        page=2,
        action="cancel",
        metadata={"title": "Example"},
    )


def test_defaults_and_entry_fields():
    clock = MutableClock(100.0)
    registry = StatusTokenRegistry(clock=clock)

    token = _create(registry)
    entry = registry.get(token)

    assert registry.ttl_seconds == DEFAULT_TTL_SECONDS
    assert registry.max_entries == DEFAULT_MAX_ENTRIES
    assert entry is not None
    assert entry.viewer_id == 123
    assert entry.task_id == "task-1"
    assert entry.binding_id == 42
    assert entry.mode == "mine"
    assert entry.view == "active"
    assert entry.page == 2
    assert entry.action == "cancel"
    assert entry.metadata == {"title": "Example"}
    assert entry.expires_at == 100.0 + DEFAULT_TTL_SECONDS


def test_none_binding_is_preserved_explicitly():
    registry = StatusTokenRegistry()
    token = registry.create(
        viewer_id=123,
        task_id="unbound-task",
        binding_id=None,
        mode="all",
        view="active",
        page=0,
        action="task",
    )

    assert registry.get(token).binding_id is None


def test_token_is_short_urlsafe_and_callback_is_well_below_limit():
    registry = StatusTokenRegistry()
    token = _create(registry)
    callback_data = build_status_callback_data("confirm", token)

    assert re.fullmatch(r"[A-Za-z0-9_-]{12}", token)
    assert callback_data == f"dst:confirm:{token}"
    assert len(callback_data.encode("utf-8")) < 32


def test_get_does_not_consume_token():
    registry = StatusTokenRegistry()
    token = _create(registry)

    assert registry.get(token) is registry.get(token)
    assert len(registry) == 1


def test_consume_removes_token_atomically():
    registry = StatusTokenRegistry()
    token = _create(registry)

    assert registry.consume(token) is not None
    assert registry.consume(token) is None
    assert registry.get(token) is None
    assert len(registry) == 0


def test_expired_token_cannot_be_read_or_consumed():
    clock = MutableClock(10.0)
    registry = StatusTokenRegistry(ttl_seconds=5, clock=clock)
    token = _create(registry)

    clock.now = 15.0

    assert registry.get(token) is None
    assert registry.consume(token) is None
    assert len(registry) == 0


def test_cleanup_removes_all_expired_entries():
    clock = MutableClock()
    registry = StatusTokenRegistry(ttl_seconds=10, clock=clock)
    _create(registry, "old-1")
    _create(registry, "old-2")
    clock.now = 11.0

    assert registry.cleanup() == 2
    assert len(registry) == 0


def test_capacity_evicts_oldest_entry_without_refreshing_on_get():
    registry = StatusTokenRegistry(max_entries=2)
    first = _create(registry, "first")
    second = _create(registry, "second")
    assert registry.get(first) is not None

    third = _create(registry, "third")

    assert registry.get(first) is None
    assert registry.get(second) is not None
    assert registry.get(third) is not None
    assert len(registry) == 2


def test_registry_instances_do_not_share_state():
    first_registry = StatusTokenRegistry()
    second_registry = StatusTokenRegistry()
    token = _create(first_registry)

    assert first_registry.get(token) is not None
    assert second_registry.get(token) is None
    assert STATUS_TOKEN_REGISTRY_KEY == "status_token_registry"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ttl_seconds": 0}, "ttl_seconds"),
        ({"max_entries": 0}, "max_entries"),
    ],
)
def test_invalid_registry_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        StatusTokenRegistry(**kwargs)


@pytest.mark.parametrize(
    ("operation", "token"),
    [("", "token"), ("bad:op", "token"), ("op", ""), ("op", "bad:token")],
)
def test_callback_rejects_unparseable_parts(operation, token):
    with pytest.raises(ValueError):
        build_status_callback_data(operation, token)


def test_callback_rejects_values_over_telegram_limit():
    with pytest.raises(ValueError, match="64-byte"):
        build_status_callback_data("x" * 60, "token")
