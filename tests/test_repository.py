"""SQLite 仓储测试。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from panda_bot.domain import GroupState, SendMode
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
