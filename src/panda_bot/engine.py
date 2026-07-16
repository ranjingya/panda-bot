"""与外部服务解耦的自由能量规则引擎。"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from panda_bot.copywriter import Copywriter
from panda_bot.domain import (
    Classification,
    GroupState,
    MessageEvent,
    SendMode,
    SignalCategory,
    TriggerDecision,
    TriggerSource,
)
from panda_bot.settings import RuleConfig


class FreedomEngine:
    """处理能量、活跃度、门槛、概率、冷却和每日上限。"""

    def __init__(
        self,
        rules: RuleConfig,
        copywriter: Copywriter,
        rng: random.Random | None = None,
    ) -> None:
        self.rules = rules
        self.copywriter = copywriter
        self.rng = rng or random.Random()
        self.timezone = ZoneInfo(rules.schedule.timezone)

    def create_state(self, chat_id: str, now: datetime) -> GroupState:
        """创建新日期的群状态。

        参数：
            chat_id: 飞书群标识。
            now: 带时区的当前事件时间。

        返回值：
            使用随机初始门槛创建的群状态。
        """

        local_now = self._localize(now)
        return GroupState(
            chat_id=chat_id,
            state_date=local_now.date(),
            energy=0.0,
            threshold=self.rng.uniform(
                self.rules.energy.threshold_min, self.rules.energy.threshold_max
            ),
            configuration_version=self.rules.version,
        )

    def ensure_current_day(self, state: GroupState, now: datetime) -> GroupState:
        """在新日期首个事件到达时初始化当日状态。"""

        local_now = self._localize(now)
        if state.state_date != local_now.date():
            return self.create_state(state.chat_id, local_now)
        return state

    def evaluate(
        self,
        event: MessageEvent,
        state: GroupState,
        classification: Classification,
        is_new_turn: bool,
    ) -> TriggerDecision:
        """处理单条文字事件并生成待提交决策。

        参数：
            event: 标准化文字事件。
            state: 当前群派生状态，函数会更新活动和能量字段。
            classification: 当前消息的规则分类结果。
            is_new_turn: 当前消息是否构成新的有效对话轮次。

        返回值：
            是否应发送、抽签信息和待提交状态参数。
        """

        now = self._localize(event.created_at)
        if not self.is_work_time(now):
            return TriggerDecision(False, "outside_work_time", classification)

        self._apply_decay(state, now)
        self._record_activity(state, event, now, is_new_turn)
        state.last_event_at = now

        if classification.category is SignalCategory.RISK:
            state.risk_until = now + timedelta(minutes=self.rules.trigger.risk_silence_minutes)
            return TriggerDecision(False, "risk_suppression_started", classification)

        if not classification.is_completion:
            return self._evaluate_time_fallback(
                state=state,
                now=now,
                classification=classification,
                is_new_turn=is_new_turn,
            )

        energy_added = self.rng.uniform(classification.score_min, classification.score_max)
        state.energy += energy_added

        reason = self._ineligible_reason(state, now)
        if reason:
            return TriggerDecision(False, reason, classification, energy_added=energy_added)

        overage = state.energy - state.threshold
        probability = self._probability(overage)
        roll = self.rng.random()
        if roll >= probability:
            return TriggerDecision(
                False,
                "probability_missed",
                classification,
                probability=probability,
                roll=roll,
                energy_added=energy_added,
            )

        copy = self.copywriter.choose_proactive(state.recent_copy_ids)
        reply = bool(event.message_id) and self.rng.random() < self.rules.trigger.reply_ratio
        send_mode = SendMode.REPLY if reply else SendMode.STANDALONE
        return TriggerDecision(
            True,
            "triggered",
            classification,
            probability=probability,
            roll=roll,
            energy_added=energy_added,
            copy=copy,
            send_mode=send_mode,
            reply_to_message_id=event.message_id if reply else None,
            cooldown_minutes=self.rng.randint(
                self.rules.trigger.cooldown_min_minutes,
                self.rules.trigger.cooldown_max_minutes,
            ),
            retained_ratio=self.rng.uniform(
                self.rules.energy.retained_ratio_min,
                self.rules.energy.retained_ratio_max,
            ),
            threshold_increment=self.rng.uniform(
                self.rules.energy.threshold_increment_min,
                self.rules.energy.threshold_increment_max,
            ),
        )

    def commit_trigger(
        self,
        state: GroupState,
        decision: TriggerDecision,
        now: datetime,
        bot_message_id: str | None,
    ) -> None:
        """在影子判定或真实发送成功后提交触发状态。

        参数：
            state: 需要更新的群状态。
            decision: 已通过发送判定的决策。
            now: 实际提交时间。
            bot_message_id: 真实发送产生的消息 ID，影子模式可为空。

        返回值：
            无返回值，状态对象会原地更新。
        """

        if not decision.should_send or decision.copy is None:
            raise ValueError("只有成功触发决策可以提交")
        local_now = self._localize(now)
        state.trigger_count += 1
        if decision.source is TriggerSource.TIME_FALLBACK:
            state.time_fallback_count += 1
        state.last_trigger_at = local_now
        state.cooldown_until = local_now + timedelta(minutes=decision.cooldown_minutes)
        state.energy *= decision.retained_ratio
        state.threshold += decision.threshold_increment
        state.recent_copy_ids.append(decision.copy.copy_id)
        state.recent_copy_ids = state.recent_copy_ids[-self.rules.presentation.recent_copy_window :]
        state.last_bot_message_id = bot_message_id
        state.interaction_until = local_now + timedelta(
            minutes=self.rules.presentation.interaction_minutes
        )
        state.interaction_replied = False

    def can_send_now(self, state: GroupState, now: datetime, decision: TriggerDecision) -> bool:
        """在真实发送前复核硬性时间、冷却、风险和上限。

        参数：
            state: 当前群派生状态。
            now: 真实发送前的当前时间。
            decision: 即将执行的触发决策。

        返回值：
            当前仍满足对应触发来源的发送条件时返回真。
        """

        local_now = self._localize(now)
        if decision.source is TriggerSource.TIME_FALLBACK:
            common_reason = self._common_ineligible_reason(state, local_now)
            return bool(
                common_reason is None
                and state.time_fallback_count < self.rules.trigger.time_fallback.daily_limit
            )
        return self._ineligible_reason(state, local_now) is None

    def can_retort(self, state: GroupState, now: datetime) -> bool:
        """判断当前是否允许发送一次互动回嘴。"""

        local_now = self._localize(now)
        if not self.is_send_window(local_now):
            return False
        return bool(
            state.last_bot_message_id
            and state.interaction_until
            and local_now <= state.interaction_until
            and not state.interaction_replied
        )

    def is_work_time(self, now: datetime) -> bool:
        """判断时间是否位于上午或下午工作时段。"""

        current = self._localize(now).time()
        schedule = self.rules.schedule
        in_morning = self._between(current, schedule.morning_start, schedule.morning_end)
        in_afternoon = self._between(current, schedule.afternoon_start, schedule.hard_stop)
        return in_morning or in_afternoon

    def is_send_window(self, now: datetime) -> bool:
        """判断时间是否位于允许主动发言的区间。"""

        current = self._localize(now).time()
        return self._between(
            current, self.rules.schedule.earliest_send, self.rules.schedule.hard_stop
        )

    def _record_activity(
        self, state: GroupState, event: MessageEvent, now: datetime, is_new_turn: bool
    ) -> None:
        """记录匿名活跃成员和合并后的对话轮次。"""

        if not is_new_turn:
            return
        state.active_senders.add(event.sender_id)
        state.daily_turns += 1
        if self._is_afternoon(now):
            state.afternoon_senders.add(event.sender_id)
            state.afternoon_turns += 1
        state.last_turn_sender = event.sender_id
        state.last_turn_at = now

    def _apply_decay(self, state: GroupState, now: datetime) -> None:
        """根据距离上一个工作事件的时间衰减自由能量。"""

        if state.last_event_at is None or state.energy <= 0:
            return
        minutes = max(0.0, (now - state.last_event_at).total_seconds() / 60)
        decay = self.rules.energy.decay
        if minutes <= decay.full_minutes:
            ratio = 1.0
        elif minutes <= decay.partial_minutes:
            ratio = decay.partial_ratio
        else:
            ratio = decay.long_ratio
        state.energy *= ratio

    def _ineligible_reason(self, state: GroupState, now: datetime) -> str | None:
        """返回当前无法发送的首个原因。"""

        common_reason = self._common_ineligible_reason(state, now)
        if common_reason:
            return common_reason
        if state.energy < state.threshold:
            return "energy_below_threshold"
        return None

    def _common_ineligible_reason(self, state: GroupState, now: datetime) -> str | None:
        """返回所有主动触发方式共享的首个禁止原因。"""

        if not self.is_send_window(now):
            return "outside_send_window"
        if state.trigger_count >= self.rules.trigger.daily_limit:
            return "daily_limit_reached"
        if state.cooldown_until and now < state.cooldown_until:
            return "cooldown_active"
        if state.risk_until and now < state.risk_until:
            return "risk_suppression_active"
        if len(state.afternoon_senders) < self.rules.activity.min_afternoon_senders:
            return "not_enough_active_senders"
        if state.afternoon_turns < self.rules.activity.min_afternoon_turns:
            return "not_enough_active_turns"
        return None

    def _evaluate_time_fallback(
        self,
        *,
        state: GroupState,
        now: datetime,
        classification: Classification,
        is_new_turn: bool,
    ) -> TriggerDecision:
        """在普通消息未命中收尾信号时评估时间兜底。

        参数：
            state: 当前群派生状态。
            now: 当前消息的本地时间。
            classification: 当前消息的规则分类结果。
            is_new_turn: 当前消息是否构成新的有效对话轮次。

        返回值：
            时间兜底触发结果；不适用时返回原消息分类原因。
        """

        fallback = self.rules.trigger.time_fallback
        if not fallback.enabled or classification.reason != "ordinary_message":
            return TriggerDecision(False, classification.reason, classification)
        if not is_new_turn:
            return TriggerDecision(False, "time_fallback_requires_new_turn", classification)

        common_reason = self._common_ineligible_reason(state, now)
        if common_reason:
            return TriggerDecision(False, common_reason, classification)
        if state.time_fallback_count >= fallback.daily_limit:
            return TriggerDecision(False, "time_fallback_daily_limit_reached", classification)

        probability = self._time_fallback_probability(now)
        if probability is None:
            return TriggerDecision(False, "time_fallback_not_started", classification)
        roll = self.rng.random()
        if roll >= probability:
            return TriggerDecision(
                False,
                "time_fallback_probability_missed",
                classification,
                probability=probability,
                roll=roll,
                source=TriggerSource.TIME_FALLBACK,
            )

        copy = self.copywriter.choose_proactive(state.recent_copy_ids)
        return TriggerDecision(
            True,
            "time_fallback_triggered",
            classification,
            probability=probability,
            roll=roll,
            copy=copy,
            send_mode=SendMode.STANDALONE,
            cooldown_minutes=self.rng.randint(
                self.rules.trigger.cooldown_min_minutes,
                self.rules.trigger.cooldown_max_minutes,
            ),
            retained_ratio=1.0,
            source=TriggerSource.TIME_FALLBACK,
        )

    def _time_fallback_probability(self, now: datetime) -> float | None:
        """选择当前时间已经生效的兜底触发概率。"""

        current = self._localize(now).time()
        probability = None
        for band in self.rules.trigger.time_fallback.probability_bands:
            if current >= time.fromisoformat(band.from_time):
                probability = band.probability
            else:
                break
        return probability

    def _probability(self, overage: float) -> float:
        """根据超过门槛的能量选择阶梯概率。"""

        probability = self.rules.trigger.probability_bands[0].probability
        for band in self.rules.trigger.probability_bands:
            if overage >= band.overage:
                probability = band.probability
            else:
                break
        return probability

    def _is_afternoon(self, now: datetime) -> bool:
        """判断当前事件是否位于下午工作时段。"""

        return self._between(
            self._localize(now).time(),
            self.rules.schedule.afternoon_start,
            self.rules.schedule.hard_stop,
        )

    def _localize(self, value: datetime) -> datetime:
        """统一将时间转换为项目时区。"""

        if value.tzinfo is None:
            return value.replace(tzinfo=self.timezone)
        return value.astimezone(self.timezone)

    @staticmethod
    def _between(current: time, start: str, end: str) -> bool:
        """判断时间是否位于左闭右开区间。"""

        return time.fromisoformat(start) <= current < time.fromisoformat(end)
