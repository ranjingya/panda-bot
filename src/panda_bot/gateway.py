"""群消息发送端口及飞书实现。"""

from __future__ import annotations

from typing import Protocol

from lark_channel import FeishuChannel


class MessageGateway(Protocol):
    """应用服务使用的最小消息发送端口。"""

    async def send_text(
        self, chat_id: str, text: str, reply_to_message_id: str | None = None
    ) -> str:
        """发送文字消息并返回平台消息 ID。"""


class FeishuMessageGateway:
    """基于飞书 Channel SDK 的文字消息网关。"""

    def __init__(self, channel: FeishuChannel) -> None:
        self._channel = channel

    async def send_text(
        self, chat_id: str, text: str, reply_to_message_id: str | None = None
    ) -> str:
        """发送独立消息或回复消息。

        参数：
            chat_id: 目标飞书群 ID。
            text: 已审核的发送文本。
            reply_to_message_id: 需要回复的源消息 ID，为空时发送独立消息。

        返回值：
            飞书返回的机器人消息 ID。
        """

        options = {"reply_to": reply_to_message_id} if reply_to_message_id else None
        result = await self._channel.send(chat_id, {"text": text}, options)
        if not result.success or not result.message_id:
            error = result.error.message if result.error else "飞书未返回消息 ID"
            raise RuntimeError(f"飞书消息发送失败：{error}")
        return result.message_id
