"""DesktopChannelAdapter 单元测试。"""

import pytest

from src.adapters.desktop_adapter import DesktopChannelAdapter


class FakeWs:
    def __init__(self):
        self.sent: list[dict] = []
        self.raise_on_send = False

    async def send_json(self, data: dict) -> None:
        if self.raise_on_send:
            raise RuntimeError("ws error")
        self.sent.append(data)


@pytest.mark.asyncio
async def test_is_connected_when_empty():
    adapter = DesktopChannelAdapter()
    assert not await adapter.is_connected()


@pytest.mark.asyncio
async def test_is_connected_after_add():
    adapter = DesktopChannelAdapter()
    adapter.add_connection("c1", FakeWs())
    assert await adapter.is_connected()


@pytest.mark.asyncio
async def test_is_connected_after_remove():
    adapter = DesktopChannelAdapter()
    adapter.add_connection("c1", FakeWs())
    adapter.remove_connection("c1")
    assert not await adapter.is_connected()


@pytest.mark.asyncio
async def test_send_text_pushes_proactive_json():
    adapter = DesktopChannelAdapter()
    ws = FakeWs()
    adapter.add_connection("c1", ws)
    await adapter.send_text("owner", "安安")
    assert ws.sent == [{"type": "proactive", "content": "安安"}]


@pytest.mark.asyncio
async def test_send_text_broadcasts_to_all_connections():
    adapter = DesktopChannelAdapter()
    ws1, ws2 = FakeWs(), FakeWs()
    adapter.add_connection("c1", ws1)
    adapter.add_connection("c2", ws2)
    await adapter.send_text("owner", "broadcast")
    assert ws1.sent == [{"type": "proactive", "content": "broadcast"}]
    assert ws2.sent == [{"type": "proactive", "content": "broadcast"}]


@pytest.mark.asyncio
async def test_send_text_removes_dead_connections():
    adapter = DesktopChannelAdapter()
    bad_ws = FakeWs()
    bad_ws.raise_on_send = True
    adapter.add_connection("dead", bad_ws)
    with pytest.raises(RuntimeError, match="没有连接成功接收消息"):
        await adapter.send_text("owner", "test")
    assert "dead" not in adapter.connections


@pytest.mark.asyncio
async def test_stop_clears_connections():
    adapter = DesktopChannelAdapter()
    adapter.add_connection("c1", FakeWs())
    await adapter.stop()
    assert not adapter.connections
