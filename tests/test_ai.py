"""未来 AI 接口的隐私边界测试。"""

from __future__ import annotations

from panda_bot.ai import build_ai_candidate


def test_ai_candidate_keeps_only_two_context_messages() -> None:
    """外部 AI 最多获得两条必要上文。"""

    candidate = build_ai_candidate("完成了", ["一", "二", "三"])
    assert candidate.context == ("二", "三")


def test_ai_candidate_redacts_identifiers_mentions_and_links() -> None:
    """候选输入必须删除链接、成员提及和长编号。"""

    candidate = build_ai_candidate("@小明 订单 ABC-123456 已完成 https://example.com/detail", [])
    assert "小明" not in candidate.text
    assert "123456" not in candidate.text
    assert "example.com" not in candidate.text
    assert candidate.text == "[成员] 订单 [编号] 已完成 [链接]"
