"""飞书事件适配器测试。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from lark_channel import Conversation, Identity, InboundMessage, ReactionEvent, TextContent

from panda_bot.adapters.feishu import FeishuEventAdapter
from panda_bot.domain import MessageEvent


@dataclass
class CaptureService:
    """记录适配结果的服务替身。"""

    messages: list[MessageEvent] = field(default_factory=list)
    reactions: list[tuple[str, bool]] = field(default_factory=list)

    async def process_message(self, event: MessageEvent) -> None:
        """保存标准化消息。"""

        self.messages.append(event)

    async def process_reaction(self, message_id: str, added: bool) -> None:
        """保存表情变化。"""

        self.reactions.append((message_id, added))


def inbound(*, sender_type: str = "user", is_bot: bool = False) -> InboundMessage:
    """创建飞书 SDK 入站消息。"""

    return InboundMessage(
        id="message",
        create_time=1_784_099_700_000,
        conversation=Conversation("chat", "group"),
        sender=Identity("sender", is_bot=is_bot, sender_type=sender_type),
        content=TextContent(text="完成了"),
        content_text="完成了",
        body_text="完成了",
        raw_content_type="text",
    )


@pytest.mark.asyncio
async def test_text_message_is_normalized() -> None:
    """文字消息应转换为领域事件并保留必要字段。"""

    service = CaptureService()
    adapter = FeishuEventAdapter(None, service)  # type: ignore[arg-type]
    await adapter.on_message(inbound())

    assert len(service.messages) == 1
    assert service.messages[0].chat_id == "chat"
    assert service.messages[0].text == "完成了"
    assert service.messages[0].created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_bot_message_is_ignored() -> None:
    """其他机器人消息不得进入业务服务。"""

    service = CaptureService()
    adapter = FeishuEventAdapter(None, service)  # type: ignore[arg-type]
    await adapter.on_message(inbound(sender_type="bot", is_bot=True))
    assert service.messages == []


@pytest.mark.asyncio
async def test_reaction_event_becomes_anonymous_delta() -> None:
    """表情增删事件只传递消息 ID 和方向。"""

    service = CaptureService()
    adapter = FeishuEventAdapter(None, service)  # type: ignore[arg-type]
    event = ReactionEvent(
        message_id="bot-message",
        operator=None,  # type: ignore[arg-type]
        emoji_type="THUMBSUP",
        action="added",
    )
    await adapter.on_reaction(event)
    assert service.reactions == [("bot-message", True)]
