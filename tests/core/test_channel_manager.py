"""ChannelManager 单元测试。"""

import pytest

from src.adapters.base import BaseAdapter, ChannelType
from src.models.message import RichMessage


class FakeAdapter(BaseAdapter):
    channel_type = ChannelType.QQ

    def __init__(self):
        super().__init__(config={})
        self.started = False
        self.stopped = False
        self.sent: list[tuple[str, str]] = []
        self._connected = True

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_text(self, chat_id: str, text: str):
        self.sent.append((chat_id, text))

    async def send_message(self, chat_id: str, message):
        self.sent.append((chat_id, message.plain_text))

    async def is_connected(self):
        return self._connected


@pytest.mark.asyncio
async def test_register_and_start_all():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.QQ, adapter)
    await mgr.start_all()
    assert adapter.started


@pytest.mark.asyncio
async def test_stop_all():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.QQ, adapter)
    await mgr.start_all()
    await mgr.stop_all()
    assert adapter.stopped


@pytest.mark.asyncio
async def test_send_to_channel():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.QQ, adapter)
    await mgr.send(ChannelType.QQ, "123", "hello")
    assert adapter.sent == [("123", "hello")]


@pytest.mark.asyncio
async def test_send_missing_adapter_raises_channel_operation_error():
    from src.core.channel_manager import ChannelManager, ChannelOperationError

    mgr = ChannelManager()

    with pytest.raises(ChannelOperationError) as exc:
        await mgr.send(ChannelType.QQ, "123", "hello")

    assert exc.value.payload["safe_details"]["reason"] == "adapter_missing"


@pytest.mark.asyncio
async def test_send_disconnected_adapter_raises_channel_operation_error():
    from src.core.channel_manager import ChannelManager, ChannelOperationError

    mgr = ChannelManager()
    adapter = FakeAdapter()
    adapter._connected = False
    mgr.register(ChannelType.QQ, adapter)

    with pytest.raises(ChannelOperationError) as exc:
        await mgr.send(ChannelType.QQ, "123", "hello")

    assert exc.value.payload["safe_details"]["reason"] == "adapter_disconnected"
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_send_message_missing_adapter_raises_channel_operation_error():
    from src.core.channel_manager import ChannelManager, ChannelOperationError

    mgr = ChannelManager()

    with pytest.raises(ChannelOperationError) as exc:
        await mgr.send_message(ChannelType.QQ, "123", RichMessage.from_text("hello"))

    assert exc.value.payload["safe_details"]["reason"] == "adapter_missing"


@pytest.mark.asyncio
async def test_send_message_disconnected_adapter_raises_channel_operation_error():
    from src.core.channel_manager import ChannelManager, ChannelOperationError

    mgr = ChannelManager()
    adapter = FakeAdapter()
    adapter._connected = False
    mgr.register(ChannelType.QQ, adapter)

    with pytest.raises(ChannelOperationError) as exc:
        await mgr.send_message(ChannelType.QQ, "123", RichMessage.from_text("hello"))

    assert exc.value.payload["safe_details"]["reason"] == "adapter_disconnected"
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_send_message_connected_adapter_sends():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.QQ, adapter)

    await mgr.send_message(ChannelType.QQ, "123", RichMessage.from_text("hello"))

    assert adapter.sent == [("123", "hello")]


@pytest.mark.asyncio
async def test_send_disabled_route_still_raises_existing_reason():
    from src.core.channel_manager import ChannelManager, ChannelOperationError

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.QQ, adapter)
    mgr.disabled_routes.add(("qq", "private"))

    with pytest.raises(ChannelOperationError) as exc:
        await mgr.send(ChannelType.QQ, "123", "hello")

    assert (
        exc.value.payload["safe_details"]["reason"]
        == "adapter route disabled by capability validation"
    )


@pytest.mark.asyncio
async def test_send_to_owner_uses_last_active():
    from src.core.channel_manager import ChannelManager
    from src.adapters.desktop_adapter import DesktopChannelAdapter

    mgr = ChannelManager()

    desktop = DesktopChannelAdapter()
    mgr.register(ChannelType.DESKTOP, desktop)

    qq = FakeAdapter()
    qq.channel_type = ChannelType.QQ
    qq.config = {"kevin_id": "222"}
    mgr.register(ChannelType.QQ, qq)

    mgr.last_active_channel = ChannelType.QQ
    await mgr.send_to_owner("hi")
    assert qq.sent == [("222", "hi")]


@pytest.mark.asyncio
async def test_send_to_owner_fallback():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()

    qq = FakeAdapter()
    qq.channel_type = ChannelType.QQ
    qq.config = {"kevin_id": "111"}
    mgr.register(ChannelType.QQ, qq)

    # No last_active_channel set — should fallback to first connected
    await mgr.send_to_owner("hi")
    assert qq.sent == [("111", "hi")]


@pytest.mark.asyncio
async def test_send_to_owner_desktop_priority():
    """Desktop 已连接时优先发 Desktop，忽略 last_active。"""
    from src.core.channel_manager import ChannelManager
    from src.adapters.desktop_adapter import DesktopChannelAdapter

    mgr = ChannelManager()

    qq = FakeAdapter()
    qq.config = {"kevin_id": "222"}
    mgr.register(ChannelType.QQ, qq)

    desktop = DesktopChannelAdapter()
    fake_ws = []

    class FakeWs:
        async def send_json(self, data):
            fake_ws.append(data)

    desktop.add_connection("c1", FakeWs())
    mgr.register(ChannelType.DESKTOP, desktop)

    mgr.last_active_channel = ChannelType.QQ  # QQ 是最后活跃，但 Desktop 已连接
    await mgr.send_to_owner("hello desktop")

    assert qq.sent == []
    assert any(m.get("content") == "hello desktop" for m in fake_ws)


@pytest.mark.asyncio
async def test_send_to_owner_desktop_disconnected_falls_back():
    """Desktop 未连接时回退到 last_active。"""
    from src.core.channel_manager import ChannelManager
    from src.adapters.desktop_adapter import DesktopChannelAdapter

    mgr = ChannelManager()

    qq = FakeAdapter()
    qq.config = {"kevin_id": "222"}
    mgr.register(ChannelType.QQ, qq)

    desktop = DesktopChannelAdapter()  # 没有连接
    mgr.register(ChannelType.DESKTOP, desktop)

    mgr.last_active_channel = ChannelType.QQ
    await mgr.send_to_owner("hi")

    assert qq.sent == [("222", "hi")]


class TestResolveDeliveryTarget:
    def test_numeric_qq_returns_as_is(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "999"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "123456")
        assert result == "123456"

    def test_non_numeric_qq_resolves_to_kevin_id_with_agent_user_status_purpose(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "999"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat", purpose="agent_user_status")
        assert result == "999"

    def test_non_numeric_qq_direct_purpose_no_fallback(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "999"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat")
        assert result is None

    def test_non_numeric_qq_direct_reply_no_fallback(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "999"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat", purpose="direct_reply")
        assert result is None

    def test_non_numeric_qq_system_diagnostic_no_fallback(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "999"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat", purpose="system_diagnostic")
        assert result is None

    def test_non_numeric_qq_owner_status_fallback(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "999"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat", purpose="owner_status")
        assert result == "999"

    def test_non_numeric_qq_no_kevin_id_returns_none(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat")
        assert result is None

    def test_non_numeric_qq_non_numeric_kevin_id_returns_none(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        qq = FakeAdapter()
        qq.config = {"kevin_id": "not_a_number"}
        mgr.register(ChannelType.QQ, qq)

        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat")
        assert result is None

    def test_qq_not_registered_returns_none_for_non_numeric(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        # No QQ adapter registered
        result = mgr.resolve_delivery_target(ChannelType.QQ, "chat")
        assert result is None

    def test_qq_not_registered_returns_as_is_for_numeric(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        # No QQ adapter registered, but numeric is valid
        result = mgr.resolve_delivery_target(ChannelType.QQ, "123456")
        assert result == "123456"

    def test_non_qq_channel_passes_through(self):
        from src.core.channel_manager import ChannelManager

        mgr = ChannelManager()
        result = mgr.resolve_delivery_target(ChannelType.DESKTOP, "any-chat-id")
        assert result == "any-chat-id"
