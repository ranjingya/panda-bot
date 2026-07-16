"""飞书 Channel SDK 事件适配器。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from lark_channel import FeishuChannel, InboundMessage, ReactionEvent, RejectEvent

from panda_bot.domain import MessageEvent
from panda_bot.service import PandaService

logger = logging.getLogger(__name__)


class FeishuEventAdapter:
    """将 SDK 事件转换为项目领域事件。"""

    def __init__(self, channel: FeishuChannel, service: PandaService) -> None:
        self.channel = channel
        self.service = service

    def register(self) -> None:
        """向 Channel SDK 注册消息、表情和错误处理器。"""

        self.channel.on("message", self.on_message)
        self.channel.on("reaction", self.on_reaction)
        self.channel.on("reject", self.on_reject)
        self.channel.on("error", self.on_error)

    async def on_message(self, message: InboundMessage) -> None:
        """过滤并标准化一条飞书消息。"""

        if message.sender_is_bot or message.sender_type in {"bot", "app", "system"}:
            return
        if message.raw_content_type != "text" and message.content.kind != "text":
            return
        created_at = self._from_timestamp(message.create_time)
        event = MessageEvent(
            event_id=message.message_id,
            message_id=message.message_id,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            sender_type=message.sender_type or "user",
            text=message.body_text or message.content_text,
            created_at=created_at,
            mentions_bot=message.mentioned_bot,
            reply_to_message_id=message.reply_to_message_id,
        )
        await self.service.process_message(event)

    async def on_reaction(self, event: ReactionEvent) -> None:
        """将表情增删事件转换为匿名计数变化。"""

        await self.service.process_reaction(event.message_id, event.action == "added")

    async def on_reject(self, event: RejectEvent) -> None:
        """记录 SDK 在业务适配器之前拒绝目标群事件的原因。"""

        if event.chat_id == self.service.runtime.target_chat_id:
            logger.warning(
                "目标群入站事件被飞书 Channel SDK 拒绝 message_id=%s reason=%s",
                event.message_id,
                event.reason,
            )
            return
        logger.debug("非目标群入站事件已由飞书 Channel SDK 拒绝 reason=%s", event.reason)

    @staticmethod
    async def on_error(error: object) -> None:
        """集中记录 Channel SDK 抛出的异常。"""

        logger.error("飞书 Channel SDK 报告错误：%s", error)

    @staticmethod
    def _from_timestamp(value: int) -> datetime:
        """将秒或毫秒时间戳转换为 UTC 时间。"""

        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=UTC)
