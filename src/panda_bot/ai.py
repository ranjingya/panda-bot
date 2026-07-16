"""未来 AI 分类、文案生成与反馈学习的稳定接口。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from panda_bot.domain import CopyChoice, SignalCategory

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_MENTION_PATTERN = re.compile(r"(?:<at[^>]*>.*?</at>|@\S+)", re.IGNORECASE)
_IDENTIFIER_PATTERN = re.compile(r"\b(?:[A-Za-z]{1,8}[-_])?\d{6,}\b")


@dataclass(frozen=True, slots=True)
class AICandidate:
    """完成脱敏并限制上下文后的 AI 分类输入。"""

    text: str
    context: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AISemanticResult:
    """AI 必须返回的结构化语义分类。"""

    category: SignalCategory
    confidence: float
    strength: int
    risk: bool


class SemanticClassifier(Protocol):
    """第二阶段语义分类适配器协议。"""

    async def classify(self, candidate: AICandidate) -> AISemanticResult:
        """分类单个最小化候选输入。"""


class LiveCopyGenerator(Protocol):
    """第三阶段现场文案生成适配器协议。"""

    async def generate(self, candidate: AICandidate, fallback: CopyChoice) -> CopyChoice:
        """在规则已允许发送后生成可审核文案。"""


class FeedbackLearner(Protocol):
    """第四阶段匿名主题权重学习协议。"""

    async def update_theme_weight(self, theme: str, reaction_count: int, replied: bool) -> None:
        """使用匿名互动汇总调整主题权重。"""


def build_ai_candidate(text: str, context: list[str]) -> AICandidate:
    """构造符合隐私边界的最小 AI 输入。

    参数：
        text: 当前候选消息正文。
        context: 当前候选消息之前的短期文字上文。

    返回值：
        删除链接、@ 和长编号且最多包含两条上文的不可变候选对象。
    """

    return AICandidate(
        text=_redact(text),
        context=tuple(_redact(item) for item in context[-2:]),
    )


def _redact(text: str) -> str:
    """删除外部 AI 不需要接触的可识别信息。"""

    redacted = _URL_PATTERN.sub("[链接]", text)
    redacted = _MENTION_PATTERN.sub("[成员]", redacted)
    redacted = _IDENTIFIER_PATTERN.sub("[编号]", redacted)
    return " ".join(redacted.split())
