"""应用编排端到端测试。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from panda_bot.classifier import RuleClassifier
from panda_bot.context import ContextBuffer
from panda_bot.copywriter import Copywriter
from panda_bot.domain import MessageEvent
from panda_bot.engine import FreedomEngine
from panda_bot.repository import SQLiteRepository
from panda_bot.service import PandaService
from panda_bot.settings import MessageCatalog, RuleConfig, RuntimeSettings

ZONE = ZoneInfo("Asia/Shanghai")


@dataclass
class FakeGateway:
    """记录发送请求的测试消息网关。"""

    sent: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def send_text(
        self, chat_id: str, text: str, reply_to_message_id: str | None = None
    ) -> str:
        """记录发送内容并返回稳定消息 ID。"""

        self.sent.append((chat_id, text, reply_to_message_id))
        return f"bot-{len(self.sent)}"


class MutableClock:
    """支持测试中推进时间的时钟。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        """返回当前测试时间。"""

        return self.now


def make_event(
    index: int,
    sender: str,
    when: datetime,
    text: str,
    *,
    mentions_bot: bool = False,
) -> MessageEvent:
    """创建应用服务测试事件。"""

    identifier = f"message-{index}"
    return MessageEvent(
        identifier,
        identifier,
        "chat",
        sender,
        "user",
        text,
        when,
        mentions_bot=mentions_bot,
    )


def build_service(
    *,
    tmp_path,
    rules: RuleConfig,
    catalog: MessageCatalog,
    deterministic_random,
    mode: str,
):
    """创建包含真实 SQLite 的测试服务。"""

    repository = SQLiteRepository(tmp_path / f"{mode}.db")
    writer = Copywriter(catalog, deterministic_random)
    engine = FreedomEngine(rules, writer, deterministic_random)
    gateway = FakeGateway()
    clock = MutableClock(datetime(2026, 7, 15, 15, 0, tzinfo=ZONE))
    runtime = RuntimeSettings(
        app_id="app",
        app_secret="secret",
        target_chat_id="chat",
        mode=mode,
    )
    service = PandaService(
        runtime=runtime,
        repository=repository,
        classifier=RuleClassifier(rules.classifier),
        context=ContextBuffer(rules.context, rules.activity),
        engine=engine,
        gateway=gateway,
        clock=clock,
    )
    return service, repository, gateway, clock


async def feed_active_group(service: PandaService, start: datetime) -> int:
    """发送足够的双成员活跃轮次。"""

    for index in range(12):
        sender = "a" if index % 2 == 0 else "b"
        await service.process_message(
            make_event(index, sender, start + timedelta(minutes=index), "普通消息")
        )
    return 12


@pytest.mark.asyncio
async def test_live_status_command_replies_without_affecting_rules(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """正式模式状态命令应回复公开信息且不累计活跃度或能量。"""

    service, repository, gateway, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()
    clock.now = datetime(2026, 7, 15, 15, 0, tzinfo=ZONE)
    command = MessageEvent(
        "status-1",
        "status-1",
        "chat",
        "a",
        "user",
        "/status",
        clock.now,
        mentions_bot=True,
    )

    await service.process_message(command)

    assert len(gateway.sent) == 1
    assert gateway.sent[0][2] == "status-1"
    assert "盼达在线" in gateway.sent[0][1]
    assert "能量：" in gateway.sent[0][1]
    assert "当前概率档位：未达到门槛" in gateway.sent[0][1]
    assert "冷却：无" in gateway.sent[0][1]
    assert "保护静默：无" in gateway.sent[0][1]
    assert "下午活跃：0 人 / 0 轮" in gateway.sent[0][1]
    assert "今天冒泡：0 次" in gateway.sent[0][1]
    assert f"规则版本：{rules.version}" in gateway.sent[0][1]
    assert "每日上限" not in gateway.sent[0][1]
    assert "关键词" not in gateway.sent[0][1]
    state = await repository.load_state("chat")
    assert state is not None
    assert state.energy == 0
    assert state.daily_turns == 0


@pytest.mark.asyncio
async def test_status_command_reports_dynamic_state_snapshot(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """状态命令应展示实时派生状态而不展示静态规则表。"""

    service, repository, gateway, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()
    clock.now = datetime(2026, 7, 15, 15, 0, tzinfo=ZONE)
    state = service.engine.create_state("chat", clock.now)
    state.energy = 60
    state.threshold = 50
    state.trigger_count = 2
    state.time_fallback_count = 1
    state.afternoon_senders = {"anonymous-a", "anonymous-b"}
    state.afternoon_turns = 16
    state.cooldown_until = clock.now + timedelta(minutes=20)
    state.risk_until = clock.now + timedelta(minutes=10)
    await repository.save_state(state)

    await service.process_message(make_event(901, "a", clock.now, "/status", mentions_bot=True))

    text = gateway.sent[0][1]
    assert "能量：60.0 / 50.0（已超过 10.0）" in text
    assert "当前概率档位：35%" in text
    assert "冷却：还剩 20 分钟" in text
    assert "保护静默：还剩 10 分钟" in text
    assert "下午活跃：2 人 / 16 轮" in text
    assert "今天冒泡：2 次（时间兜底 1 次）" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "live"])
async def test_status_command_respects_silent_modes_and_hours(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random, mode: str
) -> None:
    """影子模式和工作时间外收到状态命令时不得发送消息。"""

    service, repository, gateway, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode=mode,
    )
    await repository.initialize()
    if mode == "live":
        clock.now = datetime(2026, 7, 15, 17, 30, tzinfo=ZONE)
    command = make_event(900, "a", clock.now, "/status", mentions_bot=True)

    await service.process_message(command)

    assert gateway.sent == []


@pytest.mark.asyncio
async def test_status_command_requires_bot_mention(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """单独发送状态文本时应静默且不得进入普通消息规则。"""

    service, repository, gateway, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()

    await service.process_message(make_event(902, "a", clock.now, "/status"))

    assert gateway.sent == []
    state = await repository.load_state("chat")
    assert state is not None
    assert state.daily_turns == 0
    assert state.energy == 0


@pytest.mark.asyncio
async def test_shadow_mode_commits_without_sending(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """影子模式应完整推进状态但不调用网关。"""

    service, repository, gateway, _ = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="shadow",
    )
    await repository.initialize()
    start = datetime(2026, 7, 15, 13, 0, tzinfo=ZONE)
    index = await feed_active_group(service, start)
    for offset in range(5):
        await service.process_message(
            make_event(
                index + offset, "a", datetime(2026, 7, 15, 15, offset, tzinfo=ZONE), "完成了"
            )
        )

    state = await repository.load_state("chat")
    assert state is not None
    assert state.trigger_count == 1
    assert "a" not in state.active_senders
    assert "b" not in state.active_senders
    assert gateway.sent == []


@pytest.mark.asyncio
async def test_shadow_collects_sanitized_messages_and_decisions(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """影子模式应保存所有有效成员文字的脱敏正文与未命中决策。"""

    service, repository, gateway, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="shadow",
    )
    await repository.initialize()
    event = make_event(
        950,
        "ou_real_member",
        clock.now,
        "这块算是齐活了！找 test@example.com 看 https://example.com/123456",
    )

    await service.process_message(event)
    observations = await repository.list_shadow_observations("chat")

    assert gateway.sent == []
    assert len(observations) == 1
    observation = observations[0]
    assert observation.message_text == "这块算是齐活了！找 [邮箱] 看 [链接]"
    assert observation.classification_reason == "ordinary_message"
    assert observation.decision_reason == "not_enough_active_senders"
    assert observation.anonymous_sender != event.sender_id
    assert len(observation.anonymous_sender) == 16


@pytest.mark.asyncio
async def test_live_mode_never_persists_message_text(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """正式模式不得向影子语料表写入任何消息正文。"""

    service, repository, _, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()

    await service.process_message(make_event(951, "a", clock.now, "群里真实说法"))

    assert await repository.list_shadow_observations("chat") == []


@pytest.mark.asyncio
async def test_live_mode_sends_once_and_deduplicates(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """正式模式发送后，重复事件不得再次发送。"""

    service, repository, gateway, _ = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()
    start = datetime(2026, 7, 15, 13, 0, tzinfo=ZONE)
    index = await feed_active_group(service, start)
    final_event = None
    for offset in range(5):
        final_event = make_event(
            index + offset,
            "a",
            datetime(2026, 7, 15, 15, offset, tzinfo=ZONE),
            "完成了",
        )
        await service.process_message(final_event)
    assert final_event is not None
    assert len(gateway.sent) == 1

    await service.process_message(final_event)
    assert len(gateway.sent) == 1


@pytest.mark.asyncio
async def test_live_interaction_allows_one_retort(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """主动发言后五分钟内最多发送一次回嘴。"""

    service, repository, gateway, clock = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()
    start = datetime(2026, 7, 15, 13, 0, tzinfo=ZONE)
    index = await feed_active_group(service, start)
    for offset in range(5):
        clock.now = datetime(2026, 7, 15, 15, offset, tzinfo=ZONE)
        await service.process_message(make_event(index + offset, "a", clock.now, "完成了"))
    state = await repository.load_state("chat")
    assert state is not None
    assert state.last_bot_message_id == "bot-1"

    clock.now = datetime(2026, 7, 15, 15, 5, tzinfo=ZONE)
    reply = MessageEvent(
        "reply-1",
        "reply-1",
        "chat",
        "b",
        "user",
        "你怎么才来",
        clock.now,
        reply_to_message_id="bot-1",
    )
    await service.process_message(reply)
    assert len(gateway.sent) == 2

    second_reply = MessageEvent(
        "reply-2",
        "reply-2",
        "chat",
        "b",
        "user",
        "再说一句",
        clock.now,
        reply_to_message_id="bot-1",
    )
    await service.process_message(second_reply)
    assert len(gateway.sent) == 2


@pytest.mark.asyncio
async def test_concurrent_events_are_serialized_per_chat(
    tmp_path, rules: RuleConfig, catalog: MessageCatalog, deterministic_random
) -> None:
    """同一群并发事件必须串行更新状态，避免重复发送。"""

    service, repository, gateway, _ = build_service(
        tmp_path=tmp_path,
        rules=rules,
        catalog=catalog,
        deterministic_random=deterministic_random,
        mode="live",
    )
    await repository.initialize()
    start = datetime(2026, 7, 15, 13, 0, tzinfo=ZONE)
    index = await feed_active_group(service, start)
    events = [
        make_event(
            index + offset,
            "a",
            datetime(2026, 7, 15, 15, 0, offset, tzinfo=ZONE),
            "完成了",
        )
        for offset in range(6)
    ]

    await asyncio.gather(*(service.process_message(item) for item in events))
    assert len(gateway.sent) == 1
