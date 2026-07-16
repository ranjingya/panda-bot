"""自由能量规则引擎测试。"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from panda_bot.copywriter import Copywriter
from panda_bot.domain import Classification, MessageEvent, SignalCategory
from panda_bot.engine import FreedomEngine
from panda_bot.settings import MessageCatalog, RuleConfig

ZONE = ZoneInfo("Asia/Shanghai")


def at(hour: int, minute: int = 0) -> datetime:
    """创建固定测试日期的上海时间。"""

    return datetime(2026, 7, 15, hour, minute, tzinfo=ZONE)


def event(identifier: str, sender: str, when: datetime, text: str = "消息") -> MessageEvent:
    """创建规则引擎测试事件。"""

    return MessageEvent(identifier, identifier, "chat", sender, "user", text, when)


def completion(score: int = 20) -> Classification:
    """创建固定能量的完成分类。"""

    return Classification(SignalCategory.COMPLETION, "test", "finish", score, score)


def ordinary() -> Classification:
    """创建普通消息分类。"""

    return Classification(SignalCategory.NONE, "ordinary")


def make_engine(rules, catalog, deterministic_random) -> FreedomEngine:
    """创建完全确定的规则引擎。"""

    writer = Copywriter(catalog, deterministic_random)
    return FreedomEngine(rules, writer, deterministic_random)


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (at(8, 34), False),
        (at(8, 35), True),
        (at(11, 29), True),
        (at(11, 30), False),
        (at(12, 45), True),
        (at(17, 29), True),
        (at(17, 30), False),
    ],
)
def test_work_time_boundaries(
    rules: RuleConfig,
    catalog: MessageCatalog,
    deterministic_random,
    when: datetime,
    expected: bool,
) -> None:
    """工作时段使用左闭右开边界。"""

    engine = make_engine(rules, catalog, deterministic_random)
    assert engine.is_work_time(when) is expected


def test_send_window_starts_at_1430_and_stops_at_1730(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """最早发言时间不是定时任务，但必须限制发送资格。"""

    engine = make_engine(rules, catalog, deterministic_random)
    assert engine.is_send_window(at(14, 29)) is False
    assert engine.is_send_window(at(14, 30)) is True
    assert engine.is_send_window(at(17, 29)) is True
    assert engine.is_send_window(at(17, 30)) is False


def test_morning_energy_decays_but_affects_afternoon(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """上午能量跨午休保留部分影响。"""

    engine = make_engine(rules, catalog, deterministic_random)
    state = engine.create_state("chat", at(11, 0))
    engine.evaluate(event("1", "a", at(11, 0)), state, completion(20), True)
    assert state.energy == 20

    engine.evaluate(event("2", "b", at(12, 45)), state, ordinary(), True)
    assert state.energy == 10


def test_active_group_and_completion_can_trigger(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """两人活跃后，一人的明确完成信号可以触发。"""

    engine = make_engine(rules, catalog, deterministic_random)
    state = engine.create_state("chat", at(13, 0))
    for index in range(rules.activity.min_afternoon_turns):
        sender = "a" if index % 2 == 0 else "b"
        engine.evaluate(event(str(index), sender, at(13, index)), state, ordinary(), True)
    state.threshold = 10

    decision = engine.evaluate(
        event("finish", "a", at(15, 0), "搞定了"), state, completion(20), True
    )

    assert decision.should_send is True
    assert decision.copy is not None
    assert decision.cooldown_minutes == 30


def test_trigger_commit_sets_cooldown_and_interaction(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """成功提交后应更新次数、冷却、门槛和互动窗口。"""

    engine = make_engine(rules, catalog, deterministic_random)
    state = engine.create_state("chat", at(15, 0))
    state.threshold = 1
    state.afternoon_turns = 20
    state.afternoon_senders = {"a", "b"}
    decision = engine.evaluate(event("finish", "a", at(15, 0)), state, completion(20), True)
    engine.commit_trigger(state, decision, at(15, 0), "bot-message")

    assert state.trigger_count == 1
    assert state.cooldown_until == at(15, 30)
    assert state.interaction_until == at(15, 5)
    assert state.last_bot_message_id == "bot-message"
    assert engine.can_retort(state, at(15, 4)) is True


def test_risk_signal_blocks_following_trigger(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """明确事故在配置时长内压制主动发言。"""

    engine = make_engine(rules, catalog, deterministic_random)
    state = engine.create_state("chat", at(15, 0))
    state.threshold = 1
    state.afternoon_turns = 20
    state.afternoon_senders = {"a", "b"}
    risk = Classification(SignalCategory.RISK, "risk")
    engine.evaluate(event("risk", "a", at(15, 0)), state, risk, True)

    decision = engine.evaluate(event("finish", "b", at(15, 10)), state, completion(20), True)
    assert decision.should_send is False
    assert decision.reason == "risk_suppression_active"
    assert state.risk_until == at(15, 45)


def test_daily_limit_blocks_fifth_message(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """达到每日上限后不再允许主动发言。"""

    engine = make_engine(rules, catalog, deterministic_random)
    state = engine.create_state("chat", at(16, 0))
    state.threshold = 1
    state.energy = 100
    state.trigger_count = 4
    state.afternoon_turns = 20
    state.afternoon_senders = {"a", "b"}

    decision = engine.evaluate(event("finish", "a", at(16, 0)), state, completion(20), True)
    assert decision.should_send is False
    assert decision.reason == "daily_limit_reached"


def test_new_day_resets_state(
    rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """新日期首个事件应初始化独立状态。"""

    engine = make_engine(rules, catalog, deterministic_random)
    state = engine.create_state("chat", at(16, 0))
    state.energy = 99
    state.trigger_count = 4
    next_day = at(9, 0) + timedelta(days=1)

    reset = engine.ensure_current_day(state, next_day)
    assert reset.energy == 0
    assert reset.trigger_count == 0
    assert reset.state_date == next_day.date()
