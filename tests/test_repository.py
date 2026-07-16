"""SQLite 仓储测试。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from panda_bot.domain import (
    GroupState,
    SendMode,
    ShadowObservation,
    SignalCategory,
    TriggerSource,
)
from panda_bot.repository import SQLiteRepository


@pytest.mark.asyncio
async def test_state_round_trip_and_no_message_body(tmp_path) -> None:
    """群状态可以恢复，数据库 JSON 不包含聊天正文。"""

    path = tmp_path / "panda.db"
    repository = SQLiteRepository(path)
    await repository.initialize()
    now = datetime(2026, 7, 15, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    state = GroupState("chat", now.date(), 42.0, 58.0, active_senders={"a", "b"})
    await repository.save_state(state)

    loaded = await repository.load_state("chat")
    assert loaded is not None
    assert loaded.energy == 42
    assert loaded.active_senders == {"a", "b"}
    with sqlite3.connect(path) as connection:
        payload = connection.execute("SELECT state_json FROM group_states").fetchone()[0]
    assert "消息正文" not in payload


@pytest.mark.asyncio
async def test_event_claim_is_idempotent(tmp_path) -> None:
    """同一个平台事件只能领取一次，失败释放后可以重试。"""

    repository = SQLiteRepository(tmp_path / "panda.db")
    await repository.initialize()
    now = datetime.now().astimezone()

    assert await repository.claim_event("event", now) is True
    assert await repository.claim_event("event", now) is False
    await repository.release_event("event")
    assert await repository.claim_event("event", now) is True


@pytest.mark.asyncio
async def test_anonymous_feedback_updates(tmp_path) -> None:
    """匿名互动只记录汇总值。"""

    path = tmp_path / "panda.db"
    repository = SQLiteRepository(path)
    await repository.initialize()
    now = datetime.now().astimezone()
    await repository.record_feedback(
        chat_id="chat",
        bot_message_id="bot-message",
        copy_id="copy",
        theme="主题",
        send_mode=SendMode.STANDALONE,
        sent_at=now,
        shadow=False,
    )
    await repository.adjust_reaction("bot-message", 1)
    await repository.mark_feedback_reply("bot-message", True)

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT reaction_count, replied, retort_sent FROM feedback"
        ).fetchone()
    assert row == (1, 1, 1)


def make_observation(event_id: str, created_at: datetime, text: str) -> ShadowObservation:
    """创建仓储测试使用的完整影子观察。"""

    return ShadowObservation(
        event_id=event_id,
        chat_id="chat",
        anonymous_sender="daily-alias",
        message_text=text,
        created_at=created_at,
        is_new_turn=True,
        classification_category=SignalCategory.NONE,
        classification_reason="ordinary_message",
        signal_name=None,
        decision_reason="time_fallback_probability_missed",
        trigger_source=TriggerSource.TIME_FALLBACK,
        should_send=False,
        energy=12.0,
        threshold=50.0,
        probability=0.02,
        roll=0.8,
        energy_added=0.0,
        afternoon_senders=2,
        afternoon_turns=12,
        trigger_count=0,
        time_fallback_count=0,
        copy_id=None,
        configuration_version="1",
    )


@pytest.mark.asyncio
async def test_shadow_observation_round_trip_and_expiry(tmp_path) -> None:
    """影子语料应完整恢复，并在每次写入时自动清理过期记录。"""

    repository = SQLiteRepository(tmp_path / "panda.db")
    await repository.initialize()
    now = datetime(2026, 7, 16, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    await repository.record_shadow_observation(
        make_observation("old", now - timedelta(days=6), "旧表达"),
        retention_days=5,
    )
    await repository.record_shadow_observation(
        make_observation("current", now, "成了哈哈"),
        retention_days=5,
    )

    observations = await repository.list_shadow_observations("chat")

    assert [item.event_id for item in observations] == ["current"]
    assert observations[0].message_text == "成了哈哈"
    assert observations[0].decision_reason == "time_fallback_probability_missed"
    assert observations[0].roll == 0.8
