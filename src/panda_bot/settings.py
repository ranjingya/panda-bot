"""运行环境与外部配置加载。"""

from __future__ import annotations

import os
from datetime import time
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class RuntimeSettings(BaseModel):
    """机器人进程运行配置。"""

    app_id: str = ""
    app_secret: str = ""
    target_chat_id: str = ""
    bot_open_id: str = ""
    privacy_salt: str = ""
    mode: str = "shadow"
    database_path: Path = Path("data/panda.db")
    rules_path: Path = Path("config/rules.yaml")
    messages_path: Path = Path("config/messages.yaml")
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, *, mode: str | None = None) -> RuntimeSettings:
        """从进程环境变量创建并校验运行配置。

        参数：
            mode: 启动命令显式传入的运行模式；为空时使用模型默认值。

        返回值：
            经过类型转换和字段校验的运行配置。
        """

        environment_names = {
            "app_id": "LARK_APP_ID",
            "app_secret": "LARK_APP_SECRET",
            "target_chat_id": "PANDA_TARGET_CHAT_ID",
            "bot_open_id": "PANDA_BOT_OPEN_ID",
            "privacy_salt": "PANDA_PRIVACY_SALT",
            "database_path": "PANDA_DATABASE_PATH",
            "rules_path": "PANDA_RULES_PATH",
            "messages_path": "PANDA_MESSAGES_PATH",
            "log_level": "PANDA_LOG_LEVEL",
        }
        values = {
            field_name: os.environ[environment_name]
            for field_name, environment_name in environment_names.items()
            if environment_name in os.environ
        }
        if mode is not None:
            values["mode"] = mode
        return cls.model_validate(values)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        """校验运行模式。"""

        normalized = value.lower()
        if normalized not in {"shadow", "live"}:
            raise ValueError("运行模式只能是 shadow 或 live")
        return normalized

    def validate_credentials(self) -> None:
        """校验连接飞书所需的环境变量。"""

        missing = [
            name
            for name, value in (
                ("LARK_APP_ID", self.app_id),
                ("LARK_APP_SECRET", self.app_secret),
                ("PANDA_TARGET_CHAT_ID", self.target_chat_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"缺少运行环境变量：{', '.join(missing)}")


class ScheduleRules(BaseModel):
    """工作与发言时段。"""

    timezone: str = "Asia/Shanghai"
    morning_start: str
    morning_end: str
    afternoon_start: str
    earliest_send: str
    hard_stop: str


class ActivityRules(BaseModel):
    """群聊活跃度参数。"""

    min_afternoon_turns: int = Field(ge=1)
    min_afternoon_senders: int = Field(ge=1)
    burst_merge_seconds: int = Field(ge=1)


class ContextRules(BaseModel):
    """短期上下文参数。"""

    lookback_minutes: int = Field(ge=1)
    max_messages: int = Field(ge=1)
    retention_minutes: int = Field(ge=1)


class ShadowCollectionRules(BaseModel):
    """影子模式校准语料的采集参数。"""

    enabled: bool = True
    retention_days: int = Field(default=5, ge=1, le=30)
    max_text_chars: int = Field(default=2000, ge=100, le=10000)


class DecayRules(BaseModel):
    """自由能量衰减参数。"""

    full_minutes: int = Field(ge=0)
    partial_minutes: int = Field(ge=1)
    partial_ratio: float = Field(gt=0, le=1)
    long_ratio: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_minutes(self) -> DecayRules:
        """确保完整保留区间短于部分保留区间。"""

        if self.partial_minutes <= self.full_minutes:
            raise ValueError("partial_minutes 必须大于 full_minutes")
        return self


class EnergyRules(BaseModel):
    """能量与门槛参数。"""

    threshold_min: float
    threshold_max: float
    retained_ratio_min: float = Field(ge=0, le=1)
    retained_ratio_max: float = Field(ge=0, le=1)
    threshold_increment_min: float = Field(ge=0)
    threshold_increment_max: float = Field(ge=0)
    decay: DecayRules

    @model_validator(mode="after")
    def validate_ranges(self) -> EnergyRules:
        """校验能量参数范围。"""

        if self.threshold_max < self.threshold_min:
            raise ValueError("threshold_max 不能小于 threshold_min")
        if self.retained_ratio_max < self.retained_ratio_min:
            raise ValueError("retained_ratio_max 不能小于 retained_ratio_min")
        if self.threshold_increment_max < self.threshold_increment_min:
            raise ValueError("threshold_increment_max 不能小于最小值")
        return self


class ProbabilityBand(BaseModel):
    """超过门槛后的阶梯概率。"""

    overage: float = Field(ge=0)
    probability: float = Field(gt=0, lt=1)


class TimeProbabilityBand(BaseModel):
    """从指定时间开始生效的兜底触发概率。"""

    from_time: str
    probability: float = Field(gt=0, lt=1)

    @field_validator("from_time")
    @classmethod
    def validate_from_time(cls, value: str) -> str:
        """校验兜底概率区间的起始时间。"""

        time.fromisoformat(value)
        return value


class TimeFallbackRules(BaseModel):
    """普通消息到达时使用的低优先级时间兜底规则。"""

    enabled: bool = True
    daily_limit: int = Field(ge=1, le=4)
    probability_bands: list[TimeProbabilityBand]

    @model_validator(mode="after")
    def validate_time_fallback(self) -> TimeFallbackRules:
        """校验并排序时间兜底概率区间。"""

        if self.enabled and not self.probability_bands:
            raise ValueError("启用时间兜底时至少需要一个概率区间")
        self.probability_bands.sort(key=lambda item: time.fromisoformat(item.from_time))
        return self


class TriggerRules(BaseModel):
    """发送频率与概率参数。"""

    daily_limit: int = Field(ge=1, le=20)
    cooldown_min_minutes: int = Field(ge=1)
    cooldown_max_minutes: int = Field(ge=1)
    risk_silence_minutes: int = Field(ge=1)
    reply_ratio: float = Field(ge=0, le=1)
    probability_bands: list[ProbabilityBand]
    time_fallback: TimeFallbackRules

    @model_validator(mode="after")
    def validate_trigger(self) -> TriggerRules:
        """校验触发参数。"""

        if self.cooldown_max_minutes < self.cooldown_min_minutes:
            raise ValueError("最大冷却时间不能小于最小冷却时间")
        if not self.probability_bands:
            raise ValueError("至少需要一个概率区间")
        self.probability_bands.sort(key=lambda item: item.overage)
        return self


class SignalRule(BaseModel):
    """单类工作收尾信号。"""

    name: str
    patterns: list[str]
    score_min: int = Field(ge=1)
    score_max: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_score(self) -> SignalRule:
        """校验信号能量范围。"""

        if self.score_max < self.score_min:
            raise ValueError(f"信号 {self.name} 的 score_max 不能小于 score_min")
        return self


class ClassifierRules(BaseModel):
    """文字分类词表。"""

    signals: list[SignalRule]
    negative_patterns: list[str]
    risk_patterns: list[str]
    casual_complaints: list[str]
    ignored_patterns: list[str]


class PresentationRules(BaseModel):
    """文案与互动参数。"""

    recent_copy_window: int = Field(ge=1)
    interaction_minutes: int = Field(ge=1)


class RuleConfig(BaseModel):
    """完整规则配置。"""

    version: str
    schedule: ScheduleRules
    activity: ActivityRules
    context: ContextRules
    shadow_collection: ShadowCollectionRules = Field(default_factory=ShadowCollectionRules)
    energy: EnergyRules
    trigger: TriggerRules
    classifier: ClassifierRules
    presentation: PresentationRules


class MessageTemplate(BaseModel):
    """一条审核文案。"""

    id: str
    theme: str
    text: str


class MessageCatalog(BaseModel):
    """主动文案与回嘴文案集合。"""

    proactive: list[MessageTemplate]
    retorts: list[MessageTemplate]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> MessageCatalog:
        """确保全部文案编号唯一。"""

        identifiers = [item.id for item in [*self.proactive, *self.retorts]]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("messages.yaml 中存在重复文案编号")
        if not self.proactive or not self.retorts:
            raise ValueError("主动文案和回嘴文案都不能为空")
        return self


def _load_yaml(path: Path) -> dict:
    """读取 YAML 文件并确保根节点是对象。"""

    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件根节点必须是对象：{path}")
    return data


def load_rule_config(path: Path) -> RuleConfig:
    """加载并校验规则配置。

    参数：
        path: 规则 YAML 文件路径。

    返回值：
        通过完整校验的规则配置对象。
    """

    return RuleConfig.model_validate(_load_yaml(path))


def load_message_catalog(path: Path) -> MessageCatalog:
    """加载并校验文案目录。

    参数：
        path: 文案 YAML 文件路径。

    返回值：
        通过编号唯一性校验的文案目录。
    """

    return MessageCatalog.model_validate(_load_yaml(path))
