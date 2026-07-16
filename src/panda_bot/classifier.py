"""基于外部词表的收尾与风险分类。"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from panda_bot.domain import Classification, SignalCategory
from panda_bot.settings import ClassifierRules

_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPACE_PATTERN = re.compile(r"\s+")
_CONTEXTUAL_ENDINGS = {
    "好了",
    "成了",
    "完了",
    "结束了",
    "完成了",
    "搞定了",
    "稳定了",
    "跑通了",
    "通过了",
}


class RuleClassifier:
    """使用可配置短语和否定关系完成第一阶段分类。"""

    def __init__(self, rules: ClassifierRules) -> None:
        self._rules = rules

    @staticmethod
    def normalize(text: str) -> str:
        """清理文本中的 HTML 标签、实体和多余空白。"""

        unescaped = html.unescape(text)
        without_tags = _TAG_PATTERN.sub(" ", unescaped)
        return _SPACE_PATTERN.sub(" ", without_tags).strip().lower()

    def classify(self, text: str, context: Sequence[str] = ()) -> Classification:
        """分类当前文字消息。

        参数：
            text: 当前消息正文。
            context: 同一发送者最近的有限上文，按时间正序排列。

        返回值：
            包含类别、原因和能量范围的规则分类结果。
        """

        current = self.normalize(text)
        if not current:
            return Classification(SignalCategory.NONE, "empty_text")

        if self._contains_any(current, self._rules.risk_patterns):
            return Classification(SignalCategory.RISK, "explicit_risk")

        if self._contains_any(current, self._rules.negative_patterns):
            return Classification(SignalCategory.NONE, "negative_or_unfinished")

        if self._contains_any(current, self._rules.ignored_patterns):
            return Classification(SignalCategory.NONE, "ignored_expression")

        match = self._match_signal(current)
        if match:
            return match

        # 当前消息极短时，允许结合相同发送者的近期拆句判断。
        if current in _CONTEXTUAL_ENDINGS and context:
            combined = self.normalize("".join([context[-1], current]))
            if not self._contains_any(combined, self._rules.negative_patterns):
                match = self._match_signal(combined)
                if match:
                    return Classification(
                        category=match.category,
                        reason="completion_from_short_context",
                        signal_name=match.signal_name,
                        score_min=match.score_min,
                        score_max=match.score_max,
                    )

        if self._contains_any(current, self._rules.casual_complaints):
            return Classification(SignalCategory.NONE, "casual_complaint")
        return Classification(SignalCategory.NONE, "ordinary_message")

    def _match_signal(self, text: str) -> Classification | None:
        """按配置顺序匹配收尾信号。"""

        for signal in self._rules.signals:
            if self._contains_any(text, signal.patterns):
                return Classification(
                    category=SignalCategory.COMPLETION,
                    reason="completion_pattern",
                    signal_name=signal.name,
                    score_min=signal.score_min,
                    score_max=signal.score_max,
                )
        return None

    @staticmethod
    def _contains_any(text: str, patterns: Sequence[str]) -> bool:
        """判断文本是否包含任一大小写无关短语。"""

        return any(pattern.lower() in text for pattern in patterns)
