"""Shadow 校准报告导出测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from panda_bot.domain import ShadowObservation, SignalCategory, TriggerSource
from panda_bot.shadow_export import render_shadow_report


def test_report_contains_message_and_decision_context() -> None:
    """导出报告应让 AI 同时看到真实表达和当时规则判断。"""

    now = datetime(2026, 7, 16, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    observation = ShadowObservation(
        event_id="event",
        chat_id="chat",
        anonymous_sender="abc123",
        message_text="成了哈哈！",
        created_at=datetime(2026, 7, 16, 7, 0, tzinfo=UTC),
        is_new_turn=True,
        classification_category=SignalCategory.NONE,
        classification_reason="ordinary_message",
        signal_name=None,
        decision_reason="time_fallback_probability_missed",
        trigger_source=TriggerSource.TIME_FALLBACK,
        should_send=False,
        energy=0,
        threshold=50,
        probability=0.02,
        roll=0.5,
        energy_added=0,
        afternoon_senders=2,
        afternoon_turns=12,
        trigger_count=0,
        time_fallback_count=0,
        copy_id=None,
        configuration_version="1",
    )

    report = render_shadow_report([observation], now)

    assert "成了哈哈！" in report
    assert "ordinary_message" in report
    assert "time_fallback_probability_missed" in report
    assert "只分析群体表达" in report
    assert "报告时区：Asia/Shanghai" in report
    assert "2026-07-16T15:00:00+08:00" in report
    assert "2026-07-16T07:00:00+00:00" not in report
