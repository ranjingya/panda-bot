"""机器人启动参数测试。"""

from __future__ import annotations

import pytest
from lark_channel import Conversation, Identity, InboundMessage, PolicyGate, TextContent

from panda_bot.main import build_inbound_policy, parse_mode


@pytest.mark.parametrize("mode", ["shadow", "live"])
def test_parse_mode_accepts_explicit_runtime_modes(mode: str) -> None:
    """启动命令必须接受明确的 Shadow 和 Live 参数。"""

    assert parse_mode([mode]) == mode


def test_parse_mode_rejects_missing_mode() -> None:
    """未提供模式时应快速失败，避免误以为机器人处于另一模式。"""

    with pytest.raises(SystemExit):
        parse_mode([])


def test_inbound_policy_accepts_plain_text_only_from_target_group() -> None:
    """目标群普通消息无需艾特即可进入业务层，其他会话仍应被 SDK 拒绝。"""

    policy = build_inbound_policy("target-chat")
    gate = PolicyGate(policy)
    target_message = InboundMessage(
        id="message",
        create_time=1_784_099_700_000,
        conversation=Conversation("target-chat", "group"),
        sender=Identity("sender", sender_type="user"),
        content=TextContent(text="普通群消息"),
        content_text="普通群消息",
        body_text="普通群消息",
        raw_content_type="text",
    )
    other_group_message = InboundMessage(
        id="other-message",
        create_time=1_784_099_700_000,
        conversation=Conversation("other-chat", "group"),
        sender=Identity("sender", sender_type="user"),
        content=TextContent(text="其他群消息"),
        content_text="其他群消息",
        body_text="其他群消息",
        raw_content_type="text",
    )
    direct_message = InboundMessage(
        id="direct-message",
        create_time=1_784_099_700_000,
        conversation=Conversation("direct-chat", "p2p"),
        sender=Identity("sender", sender_type="user"),
        content=TextContent(text="私聊消息"),
        content_text="私聊消息",
        body_text="私聊消息",
        raw_content_type="text",
    )

    assert gate.evaluate(target_message).allowed is True
    assert gate.evaluate(other_group_message).reason == "policy_group_not_in_allowlist"
    assert gate.evaluate(direct_message).reason == "policy_dm_disabled"
