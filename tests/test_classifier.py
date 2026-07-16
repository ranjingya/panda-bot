"""规则分类器测试。"""

from __future__ import annotations

import pytest

from panda_bot.classifier import RuleClassifier
from panda_bot.domain import SignalCategory
from panda_bot.settings import RuleConfig


@pytest.mark.parametrize(
    ("text", "signal_name"),
    [
        ("这个终于跑通了", "verified"),
        ("额滴使命完成了", "overall_finish"),
        ("京东 pop 发布完成", "delivery"),
        ("流程模块分好了", "general_finish"),
    ],
)
def test_completion_signals(rules: RuleConfig, text: str, signal_name: str) -> None:
    """明确收尾表达应返回对应能量类型。"""

    result = RuleClassifier(rules.classifier).classify(text)
    assert result.category is SignalCategory.COMPLETION
    assert result.signal_name == signal_name


@pytest.mark.parametrize(
    "text",
    ["还没完成", "这个完成不了", "bug 超级多", "是肯定要返工的"],
)
def test_negative_expressions_do_not_score(rules: RuleConfig, text: str) -> None:
    """否定和未完成表达不得产生能量。"""

    result = RuleClassifier(rules.classifier).classify(text)
    assert result.category is SignalCategory.NONE
    assert result.reason == "negative_or_unfinished"


def test_casual_complaint_does_not_suppress(rules: RuleConfig) -> None:
    """日常吐槽只作为普通消息处理。"""

    result = RuleClassifier(rules.classifier).classify("烦死了，这玩意难绷")
    assert result.category is SignalCategory.NONE
    assert result.reason == "casual_complaint"


def test_explicit_risk_starts_suppression(rules: RuleConfig) -> None:
    """明确线上事故应返回风险分类。"""

    result = RuleClassifier(rules.classifier).classify("线上故障，正在紧急修复")
    assert result.category is SignalCategory.RISK


def test_downwork_expression_is_ignored(rules: RuleConfig) -> None:
    """下班相关表达不属于收尾信号。"""

    result = RuleClassifier(rules.classifier).classify("是不是可以提前下班了")
    assert result.category is SignalCategory.NONE
    assert result.reason == "ignored_expression"


def test_split_sentence_uses_same_sender_context(rules: RuleConfig) -> None:
    """短句可以和同发送者上文拼接为明确收尾表达。"""

    result = RuleClassifier(rules.classifier).classify("好了", ["这个终于改"])
    assert result.category is SignalCategory.COMPLETION
    assert result.reason == "completion_from_short_context"


def test_unrelated_short_message_does_not_reuse_old_completion(rules: RuleConfig) -> None:
    """普通短句不得重复利用上一次完成信号。"""

    result = RuleClassifier(rules.classifier).classify("哈哈", ["已经完成了"])
    assert result.category is SignalCategory.NONE
