"""短期上下文与拆句合并测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from panda_bot.context import ContextBuffer
from panda_bot.domain import MessageEvent
from panda_bot.settings import RuleConfig


def make_event(event_id: str, sender: str, text: str, when: datetime) -> MessageEvent:
    """创建上下文测试事件。"""

    return MessageEvent(event_id, event_id, "chat", sender, "user", text, when)


def test_burst_messages_are_one_turn(rules: RuleConfig) -> None:
    """同成员一分钟内的连续消息只形成一个对话轮次。"""

    buffer = ContextBuffer(rules.context, rules.activity)
    start = datetime(2026, 7, 15, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    first_turn, _ = buffer.add(make_event("1", "a", "这个", start))
    second_turn, context = buffer.add(make_event("2", "a", "改好了", start + timedelta(seconds=30)))

    assert first_turn is True
    assert second_turn is False
    assert context == ["这个"]


def test_other_sender_starts_new_turn(rules: RuleConfig) -> None:
    """不同成员连续发言应分别计入活跃轮次。"""

    buffer = ContextBuffer(rules.context, rules.activity)
    start = datetime(2026, 7, 15, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    buffer.add(make_event("1", "a", "一", start))

    is_new_turn, context = buffer.add(make_event("2", "b", "二", start + timedelta(seconds=10)))

    assert is_new_turn is True
    assert context == []


def test_expired_content_is_removed(rules: RuleConfig) -> None:
    """超过内存保留期的正文不得进入上下文。"""

    buffer = ContextBuffer(rules.context, rules.activity)
    start = datetime(2026, 7, 15, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    buffer.add(make_event("1", "a", "旧消息", start))

    _, context = buffer.add(make_event("2", "a", "新消息", start + timedelta(minutes=31)))
    assert context == []
