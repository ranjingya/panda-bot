"""未来 AI 分类、文案生成与反馈学习的稳定接口。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from panda_bot.domain import CopyChoice, SignalCategory

_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_MENTION_PATTERN = re.compile(
    r"(?:<at[^>]*>.*?</at>|@[^\s，。！？、；：,.!?;:]+)",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_FEISHU_ID_PATTERN = re.compile(r"\b(?:ou|oc|on|cli)_[A-Za-z0-9]+\b")
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
        text=redact_for_ai(text),
        context=tuple(redact_for_ai(item) for item in context[-2:]),
    )


def redact_for_ai(text: str, max_chars: int | None = None) -> str:
    """脱敏校准语料，同时保留群聊语气、标点和换行。

    参数：
        text: 需要脱敏的群聊文字。
        max_chars: 允许保留的最大字符数；为空时不截断。

    返回值：
        删除链接、邮箱、成员提及、飞书标识和长编号后的文字。
    """

    redacted = _URL_PATTERN.sub("[链接]", text)
    redacted = _EMAIL_PATTERN.sub("[邮箱]", redacted)
    redacted = _MENTION_PATTERN.sub("[成员]", redacted)
    redacted = _FEISHU_ID_PATTERN.sub("[飞书标识]", redacted)
    redacted = _IDENTIFIER_PATTERN.sub("[编号]", redacted)
    lines = [" ".join(line.split()) for line in redacted.splitlines()]
    cleaned = "\n".join(line for line in lines if line).strip()
    if max_chars is not None:
        return cleaned[:max_chars]
    return cleaned
