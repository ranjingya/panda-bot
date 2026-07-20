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

    result = RuleClassifier(rules.classifier).classify("好了", ["这个终于改"], is_new_turn=False)
    assert result.category is SignalCategory.COMPLETION
    assert result.reason == "completion_from_short_context"


def test_unrelated_short_message_does_not_reuse_old_completion(rules: RuleConfig) -> None:
    """普通短句不得重复利用上一次完成信号。"""

    result = RuleClassifier(rules.classifier).classify("哈哈", ["已经完成了"])
    assert result.category is SignalCategory.NONE


@pytest.mark.parametrize(
    "text",
    [
        "终于改好一个了",
        "竞品分析页面搞完了",
        "收工了",
        "mac采购过了",
        "就这样了不动了",
    ],
)
def test_shadow_review_completion_phrases(rules: RuleConfig, text: str) -> None:
    """Shadow 校准确认的真实工作收尾表达应产生能量。"""

    result = RuleClassifier(rules.classifier).classify(text)
    assert result.category is SignalCategory.COMPLETION
    assert result.signal_name == "general_finish"


def test_ambiguous_ending_requires_same_turn_context(rules: RuleConfig) -> None:
    """含语气词的模糊落点没有同轮工作上文时不得产生能量。"""

    classifier = RuleClassifier(rules.classifier)
    isolated = classifier.classify("成了哈哈哈", ["很久以前的普通消息"], is_new_turn=True)
    contextual = classifier.classify("好了哈哈哈", ["这个终于改"], is_new_turn=False)

    assert isolated.category is SignalCategory.NONE
    assert contextual.category is SignalCategory.COMPLETION
    assert contextual.reason == "completion_from_short_context"


def test_counterfactual_context_blocks_completion(rules: RuleConfig) -> None:
    """反事实拆句中的完成字样不得被当成真实收尾。"""

    result = RuleClassifier(rules.classifier).classify(
        "早就搞出来了", ["要有一半勤奋"], is_new_turn=False
    )

    assert result.category is SignalCategory.NONE
    assert result.reason == "blocked_by_short_context"


@pytest.mark.parametrize("text", ["跑通了是跑通了", "完成了，不过还要再看看"])
def test_qualified_completion_does_not_score(rules: RuleConfig, text: str) -> None:
    """让步式或带明确转折的完成表达不得直接产生能量。"""

    result = RuleClassifier(rules.classifier).classify(text)
    assert result.category is SignalCategory.NONE
    assert result.reason == "qualified_completion"


def test_same_turn_similar_completion_only_scores_once(rules: RuleConfig) -> None:
    """同一轮内复读相似完成表达时不得重复增加能量。"""

    result = RuleClassifier(rules.classifier).classify("完成了", ["搞定了"], is_new_turn=False)

    assert result.category is SignalCategory.NONE
    assert result.reason == "repeated_completion"


@pytest.mark.parametrize(
    "text",
    ["还没结束", "总感觉有什么事没做", "我明天改bug", "我在搞设计"],
)
def test_shadow_review_unfinished_phrases_do_not_use_fallback(rules: RuleConfig, text: str) -> None:
    """Shadow 样本里的未完成或进行中表达应明确排除。"""

    result = RuleClassifier(rules.classifier).classify(text)
    assert result.category is SignalCategory.NONE
    assert result.reason == "negative_or_unfinished"


def test_negated_risk_does_not_start_suppression(rules: RuleConfig) -> None:
    """风险否定句不得开启保护静默。"""

    result = RuleClassifier(rules.classifier).classify("不是线上故障，也没有影响业务")
    assert result.category is SignalCategory.NONE
    assert result.reason == "negated_risk"


def test_other_explicit_risk_survives_risk_negation(rules: RuleConfig) -> None:
    """同句否定一种风险时，另一种明确风险仍应开启保护静默。"""

    result = RuleClassifier(rules.classifier).classify("不是线上故障，是生产故障")
    assert result.category is SignalCategory.RISK


@pytest.mark.parametrize("text", ["mimo炸了啊", "工具卡死了", "玩崩了"])
def test_tool_complaints_are_not_business_risks(rules: RuleConfig, text: str) -> None:
    """工具故障和口语化吐槽缺少业务影响时不得触发保护静默。"""

    result = RuleClassifier(rules.classifier).classify(text)
    assert result.category is SignalCategory.NONE
