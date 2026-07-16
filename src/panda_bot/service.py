"""机器人应用编排服务。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, time

from panda_bot.classifier import RuleClassifier
from panda_bot.context import ContextBuffer
from panda_bot.domain import MessageEvent, SendMode
from panda_bot.engine import FreedomEngine
from panda_bot.gateway import MessageGateway
from panda_bot.repository import SQLiteRepository
from panda_bot.settings import RuntimeSettings

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "🐼 大家好，我是盼达。\n"
    "平时不催活，也不准点报时。等我感觉今天差不多有戏了，可能会出来说两句。\n"
    "至于什么时候出现——我也不知道，嘻嘻。"
)
STATUS_COMMAND = "/status"


class PandaService:
    """协调幂等、上下文、规则引擎、存储和消息发送。"""

    def __init__(
        self,
        *,
        runtime: RuntimeSettings,
        repository: SQLiteRepository,
        classifier: RuleClassifier,
        context: ContextBuffer,
        engine: FreedomEngine,
        gateway: MessageGateway,
        clock: Callable[[], datetime],
    ) -> None:
        self.runtime = runtime
        self.repository = repository
        self.classifier = classifier
        self.context = context
        self.engine = engine
        self.gateway = gateway
        self.clock = clock
        self._chat_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def process_message(self, event: MessageEvent) -> None:
        """处理一条标准化飞书文字事件。

        参数：
            event: 通过飞书适配器标准化后的文字事件。

        返回值：
            无返回值；状态、日志和可选消息发送均在流程内完成。
        """

        if event.chat_id != self.runtime.target_chat_id:
            return
        if event.sender_type in {"bot", "app", "system"}:
            return
        if self.runtime.bot_open_id and event.sender_id == self.runtime.bot_open_id:
            return
        if not event.text.strip():
            return

        async with self._chat_locks[event.chat_id]:
            await self._process_message_locked(event)

    async def _process_message_locked(self, event: MessageEvent) -> None:
        """在群级串行锁内处理消息并维护状态一致性。"""

        claimed = await self.repository.claim_event(event.event_id, event.created_at)
        if not claimed:
            logger.info("忽略重复事件 event_id=%s", event.event_id)
            return

        try:
            state = await self.repository.load_state(event.chat_id)
            if state is None:
                state = self.engine.create_state(event.chat_id, event.created_at)
            else:
                previous_date = state.state_date
                state = self.engine.ensure_current_day(state, event.created_at)
                if state.state_date != previous_date:
                    self.context.clear_chat(event.chat_id)
                    logger.info("群状态已切换到新日期 chat_id=%s", event.chat_id)

            if await self._try_status_command(event, state):
                await self.repository.save_state(state)
                return

            if await self._try_retort(event, state):
                await self.repository.save_state(state)
                return

            is_new_turn, recent_context = self.context.add(event)
            anonymous_sender = self._anonymous_sender(event.sender_id)
            if (
                is_new_turn
                and state.last_turn_sender == anonymous_sender
                and state.last_turn_at
                and 0
                <= (event.created_at - state.last_turn_at).total_seconds()
                <= self.engine.rules.activity.burst_merge_seconds
            ):
                # 进程重启后仍使用持久化的匿名轮次信息完成拆句合并。
                is_new_turn = False
            classification = self.classifier.classify(event.text, recent_context)
            activity_event = replace(event, sender_id=anonymous_sender)
            decision = self.engine.evaluate(activity_event, state, classification, is_new_turn)
            logger.info(
                "事件决策完成 event_id=%s category=%s reason=%s energy=%.2f threshold=%.2f",
                event.event_id,
                classification.category.value,
                decision.reason,
                state.energy,
                state.threshold,
            )

            if decision.should_send and decision.copy:
                await self._handle_trigger(event, state, decision)

            await self.repository.save_state(state)
        except Exception:
            await self.repository.release_event(event.event_id)
            logger.exception("事件处理失败并已释放幂等记录 event_id=%s", event.event_id)
            raise

    def _anonymous_sender(self, sender_id: str) -> str:
        """将飞书成员 ID 转换为不可逆的项目内匿名标识。"""

        salt = self.runtime.privacy_salt or self.runtime.app_secret
        value = f"{salt}:{sender_id}".encode()
        return hashlib.sha256(value).hexdigest()

    async def process_reaction(self, bot_message_id: str, added: bool) -> None:
        """匿名记录机器人消息获得或失去的表情回应。"""

        await self.repository.adjust_reaction(bot_message_id, 1 if added else -1)
        logger.info("匿名表情反馈已更新 added=%s", added)

    async def ensure_welcome(self) -> None:
        """正式模式首次启动时发送一次欢迎语。"""

        key = f"welcome_sent:{self.runtime.target_chat_id}"
        if await self.repository.get_metadata(key):
            return
        if self.runtime.mode == "shadow":
            logger.info("影子模式跳过首次欢迎消息")
            return
        message_id = await self.gateway.send_text(self.runtime.target_chat_id, WELCOME_TEXT)
        await self.repository.set_metadata(key, message_id)
        logger.info("首次欢迎消息发送成功")

    async def _try_status_command(self, event: MessageEvent, state) -> bool:
        """识别并处理不影响核心规则的状态查询命令。

        参数：
            event: 当前标准化文字事件。
            state: 当前群派生状态。

        返回值：
            当前消息属于状态命令时返回真，无论是否因静默规则实际回复。
        """

        if event.text.strip().lower() != STATUS_COMMAND:
            return False

        now = self.clock()
        if not self.engine.is_work_time(now):
            logger.info("状态命令位于静默时段，已忽略 event_id=%s", event.event_id)
            return True
        if self.runtime.mode == "shadow":
            logger.info("影子模式收到状态命令但不发送 event_id=%s", event.event_id)
            return True

        status_text = self._build_status_text(state, now)
        await self.gateway.send_text(event.chat_id, status_text, event.message_id)
        logger.info("状态命令回复成功 event_id=%s", event.event_id)
        return True

    def _build_status_text(self, state, now: datetime) -> str:
        """构建不公开隐藏规则和个人数据的状态文本。

        参数：
            state: 当前群派生状态。
            now: 命令处理时的当前时间。

        返回值：
            包含运行阶段、当日出现次数和规则版本的简短文本。
        """

        local_now = now.astimezone(self.engine.timezone)
        schedule = self.engine.rules.schedule
        current = local_now.time()
        if current < time.fromisoformat(schedule.morning_end):
            phase = "上午观察中"
        elif current < time.fromisoformat(schedule.earliest_send):
            phase = "下午积累中"
        else:
            phase = "随机冒泡时段"
        return (
            "🐼 盼达在线\n"
            f"状态：{phase}\n"
            f"今天冒泡：{state.trigger_count} 次\n"
            f"规则版本：{self.engine.rules.version}"
        )

    async def _try_retort(self, event: MessageEvent, state) -> bool:
        """在受控互动窗口内尝试发送一次回嘴。"""

        is_reply = bool(
            state.last_bot_message_id and event.reply_to_message_id == state.last_bot_message_id
        )
        if not (is_reply or event.mentions_bot):
            return False
        if not self.engine.can_retort(state, self.clock()):
            return False

        await self.repository.mark_feedback_reply(state.last_bot_message_id, False)
        if self.runtime.mode == "shadow":
            return False

        copy = self.engine.copywriter.choose_retort()
        await self.gateway.send_text(event.chat_id, copy.text, event.message_id)
        state.interaction_replied = True
        await self.repository.mark_feedback_reply(state.last_bot_message_id, True)
        logger.info("互动回嘴发送成功 copy_id=%s", copy.copy_id)
        return True

    async def _handle_trigger(self, event, state, decision) -> None:
        """提交影子触发或执行真实发送。"""

        now = self.clock()
        if self.runtime.mode == "shadow":
            shadow_id = f"shadow:{event.event_id}"
            self.engine.commit_trigger(state, decision, now, None)
            await self.repository.record_feedback(
                chat_id=event.chat_id,
                bot_message_id=shadow_id,
                copy_id=decision.copy.copy_id,
                theme=decision.copy.theme,
                send_mode=decision.send_mode,
                sent_at=now,
                shadow=True,
            )
            logger.info(
                "影子模式命中发送 decision_copy=%s mode=%s",
                decision.copy.copy_id,
                decision.send_mode.value,
            )
            return

        if not self.engine.can_send_now(state, now, decision):
            logger.warning("发送前复核未通过，取消本次发送")
            return

        reply_to = decision.reply_to_message_id if decision.send_mode is SendMode.REPLY else None
        bot_message_id = await self.gateway.send_text(event.chat_id, decision.copy.text, reply_to)
        self.engine.commit_trigger(state, decision, now, bot_message_id)
        await self.repository.record_feedback(
            chat_id=event.chat_id,
            bot_message_id=bot_message_id,
            copy_id=decision.copy.copy_id,
            theme=decision.copy.theme,
            send_mode=decision.send_mode,
            sent_at=now,
            shadow=False,
        )
        logger.info(
            "机器人主动发言成功 copy_id=%s mode=%s count=%s",
            decision.copy.copy_id,
            decision.send_mode.value,
            state.trigger_count,
        )
