"""项目领域模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class SignalCategory(StrEnum):
    """消息信号分类。"""

    NONE = "none"
    COMPLETION = "completion"
    RISK = "risk"


class SendMode(StrEnum):
    """机器人消息发送形式。"""

    STANDALONE = "standalone"
    REPLY = "reply"


@dataclass(frozen=True, slots=True)
class MessageEvent:
    """标准化后的群聊文字事件。"""

    event_id: str
    message_id: str
    chat_id: str
    sender_id: str
    sender_type: str
    text: str
    created_at: datetime
    mentions_bot: bool = False
    reply_to_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class Classification:
    """规则分类结果。"""

    category: SignalCategory
    reason: str
    signal_name: str | None = None
    score_min: int = 0
    score_max: int = 0

    @property
    def is_completion(self) -> bool:
        """判断当前结果是否属于工作收尾信号。"""

        return self.category is SignalCategory.COMPLETION


@dataclass(frozen=True, slots=True)
class CopyChoice:
    """已选择的文案。"""

    copy_id: str
    theme: str
    text: str


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    """单个事件的触发决策。"""

    should_send: bool
    reason: str
    classification: Classification
    probability: float = 0.0
    roll: float | None = None
    energy_added: float = 0.0
    copy: CopyChoice | None = None
    send_mode: SendMode = SendMode.STANDALONE
    reply_to_message_id: str | None = None
    cooldown_minutes: int = 0
    retained_ratio: float = 0.0
    threshold_increment: float = 0.0


@dataclass(slots=True)
class GroupState:
    """单个群聊需要持久化的派生状态。"""

    chat_id: str
    state_date: date
    energy: float
    threshold: float
    trigger_count: int = 0
    last_event_at: datetime | None = None
    last_trigger_at: datetime | None = None
    cooldown_until: datetime | None = None
    risk_until: datetime | None = None
    active_senders: set[str] = field(default_factory=set)
    afternoon_senders: set[str] = field(default_factory=set)
    daily_turns: int = 0
    afternoon_turns: int = 0
    recent_copy_ids: list[str] = field(default_factory=list)
    last_turn_sender: str | None = None
    last_turn_at: datetime | None = None
    last_bot_message_id: str | None = None
    interaction_until: datetime | None = None
    interaction_replied: bool = False
    configuration_version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        """将状态转换为可写入 JSON 的字典。"""

        data = asdict(self)
        data["state_date"] = self.state_date.isoformat()
        for field_name in (
            "last_event_at",
            "last_trigger_at",
            "cooldown_until",
            "risk_until",
            "last_turn_at",
            "interaction_until",
        ):
            value = getattr(self, field_name)
            data[field_name] = value.isoformat() if value else None
        data["active_senders"] = sorted(self.active_senders)
        data["afternoon_senders"] = sorted(self.afternoon_senders)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GroupState:
        """从数据库 JSON 字典恢复群状态。"""

        values = dict(data)
        values["state_date"] = date.fromisoformat(values["state_date"])
        for field_name in (
            "last_event_at",
            "last_trigger_at",
            "cooldown_until",
            "risk_until",
            "last_turn_at",
            "interaction_until",
        ):
            if values.get(field_name):
                values[field_name] = datetime.fromisoformat(values[field_name])
        values["active_senders"] = set(values.get("active_senders", []))
        values["afternoon_senders"] = set(values.get("afternoon_senders", []))
        return cls(**values)
