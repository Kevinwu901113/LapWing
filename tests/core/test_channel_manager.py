"""ChannelManager 单元测试。"""

import pytest

from src.adapters.base import BaseAdapter, ChannelType


class FakeAdapter(BaseAdapter):
    channel_type = ChannelType.TELEGRAM

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

    async def is_connected(self):
        return self._connected


@pytest.mark.asyncio
async def test_register_and_start_all():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.TELEGRAM, adapter)
    await mgr.start_all()
    assert adapter.started


@pytest.mark.asyncio
async def test_stop_all():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.TELEGRAM, adapter)
    await mgr.start_all()
    await mgr.stop_all()
    assert adapter.stopped


@pytest.mark.asyncio
async def test_send_to_channel():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()
    adapter = FakeAdapter()
    mgr.register(ChannelType.TELEGRAM, adapter)
    await mgr.send(ChannelType.TELEGRAM, "123", "hello")
    assert adapter.sent == [("123", "hello")]


@pytest.mark.asyncio
async def test_send_to_kevin_uses_last_active():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()

    tg = FakeAdapter()
    tg.channel_type = ChannelType.TELEGRAM
    tg.config = {"kevin_id": "111"}
    mgr.register(ChannelType.TELEGRAM, tg)

    qq = FakeAdapter()
    qq.channel_type = ChannelType.QQ
    qq.config = {"kevin_id": "222"}
    mgr.register(ChannelType.QQ, qq)

    mgr.last_active_channel = ChannelType.QQ
    await mgr.send_to_kevin("hi")
    assert qq.sent == [("222", "hi")]
    assert tg.sent == []


@pytest.mark.asyncio
async def test_send_to_kevin_fallback():
    from src.core.channel_manager import ChannelManager

    mgr = ChannelManager()

    tg = FakeAdapter()
    tg.channel_type = ChannelType.TELEGRAM
    tg.config = {"kevin_id": "111"}
    mgr.register(ChannelType.TELEGRAM, tg)

    # No last_active_channel set — should fallback to first connected
    await mgr.send_to_kevin("hi")
    assert tg.sent == [("111", "hi")]
