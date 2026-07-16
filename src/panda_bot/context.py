"""短期内存上下文与对话轮次合并。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from panda_bot.domain import MessageEvent
from panda_bot.settings import ActivityRules, ContextRules


@dataclass(frozen=True, slots=True)
class RecentMessage:
    """仅存在于内存中的近期文字消息。"""

    sender_id: str
    text: str
    created_at: datetime


class ContextBuffer:
    """按群维护具有自动过期能力的短期消息缓存。"""

    def __init__(self, context: ContextRules, activity: ActivityRules) -> None:
        self._context = context
        self._activity = activity
        self._messages: dict[str, deque[RecentMessage]] = defaultdict(deque)

    def add(self, event: MessageEvent) -> tuple[bool, list[str]]:
        """加入消息并返回轮次判断和同发送者上文。

        参数：
            event: 当前标准化文字事件。

        返回值：
            二元组，第一个值表示是否为新对话轮次，第二个值是可用于分类的近期上文。
        """

        queue = self._messages[event.chat_id]
        retention_boundary = event.created_at - timedelta(minutes=self._context.retention_minutes)
        while queue and queue[0].created_at < retention_boundary:
            queue.popleft()

        lookback_boundary = event.created_at - timedelta(minutes=self._context.lookback_minutes)
        context = [
            item.text
            for item in queue
            if item.sender_id == event.sender_id and item.created_at >= lookback_boundary
        ][-self._context.max_messages :]

        is_new_turn = True
        if queue:
            previous = queue[-1]
            interval = (event.created_at - previous.created_at).total_seconds()
            is_new_turn = not (
                previous.sender_id == event.sender_id
                and 0 <= interval <= self._activity.burst_merge_seconds
            )

        queue.append(RecentMessage(event.sender_id, event.text, event.created_at))
        return is_new_turn, context

    def clear_chat(self, chat_id: str) -> None:
        """清除指定群的临时正文缓存。"""

        self._messages.pop(chat_id, None)
