"""SQLite 派生状态、影子校准语料与匿名反馈仓储。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from panda_bot.domain import (
    GroupState,
    SendMode,
    ShadowObservation,
    SignalCategory,
    TriggerSource,
)


class SQLiteRepository:
    """使用短连接和进程内锁管理 SQLite 数据。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """创建数据目录和数据库表。"""

        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    async def load_state(self, chat_id: str) -> GroupState | None:
        """读取指定群的派生状态。"""

        async with self._lock:
            return await asyncio.to_thread(self._load_state_sync, chat_id)

    async def save_state(self, state: GroupState) -> None:
        """原子保存指定群的完整派生状态。"""

        async with self._lock:
            await asyncio.to_thread(self._save_state_sync, state)

    async def claim_event(self, event_id: str, created_at: datetime) -> bool:
        """尝试领取事件，重复事件返回 False。"""

        async with self._lock:
            return await asyncio.to_thread(self._claim_event_sync, event_id, created_at)

    async def release_event(self, event_id: str) -> None:
        """处理失败时释放事件，允许平台重试。"""

        async with self._lock:
            await asyncio.to_thread(self._release_event_sync, event_id)

    async def cleanup_events(self, now: datetime, retention_days: int = 2) -> int:
        """删除超过保留期的幂等事件记录。"""

        boundary = now - timedelta(days=retention_days)
        async with self._lock:
            return await asyncio.to_thread(self._cleanup_events_sync, boundary)

    async def record_shadow_observation(
        self, observation: ShadowObservation, retention_days: int
    ) -> None:
        """保存一条脱敏影子观察并清理过期语料。

        参数：
            observation: 已完成脱敏的成员消息与规则决策快照。
            retention_days: 语料最多保留的自然日数。

        返回值：
            无返回值；写入与过期清理在同一事务中完成。
        """

        async with self._lock:
            await asyncio.to_thread(
                self._record_shadow_observation_sync,
                observation,
                retention_days,
            )

    async def list_shadow_observations(
        self, chat_id: str, since: datetime | None = None
    ) -> list[ShadowObservation]:
        """按时间顺序读取指定群的脱敏影子观察。

        参数：
            chat_id: 需要读取的目标群标识。
            since: 可选的最早消息时间，包含边界时刻。

        返回值：
            可以交给本地导出器和后续 AI 分析流程的观察列表。
        """

        async with self._lock:
            return await asyncio.to_thread(self._list_shadow_observations_sync, chat_id, since)

    async def record_feedback(
        self,
        *,
        chat_id: str,
        bot_message_id: str,
        copy_id: str,
        theme: str,
        send_mode: SendMode,
        sent_at: datetime,
        shadow: bool,
    ) -> None:
        """记录一条主动文案的匿名效果基线。"""

        async with self._lock:
            await asyncio.to_thread(
                self._record_feedback_sync,
                chat_id,
                bot_message_id,
                copy_id,
                theme,
                send_mode,
                sent_at,
                shadow,
            )

    async def mark_feedback_reply(self, bot_message_id: str, retort_sent: bool) -> None:
        """标记机器人消息在互动窗口内收到回复。"""

        async with self._lock:
            await asyncio.to_thread(self._mark_feedback_reply_sync, bot_message_id, retort_sent)

    async def adjust_reaction(self, bot_message_id: str, delta: int) -> None:
        """按消息 ID 匿名调整表情回应总数。"""

        async with self._lock:
            await asyncio.to_thread(self._adjust_reaction_sync, bot_message_id, delta)

    async def get_metadata(self, key: str) -> str | None:
        """读取单个运行元数据。"""

        async with self._lock:
            return await asyncio.to_thread(self._get_metadata_sync, key)

    async def set_metadata(self, key: str, value: str) -> None:
        """写入单个运行元数据。"""

        async with self._lock:
            await asyncio.to_thread(self._set_metadata_sync, key, value)

    def _connect(self) -> sqlite3.Connection:
        """创建带行对象和外键支持的 SQLite 连接。"""

        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _utc_isoformat(value: datetime) -> str:
        """将带时区时间统一转换为可排序的 UTC ISO 字符串。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("持久化时间必须包含时区信息")
        return value.astimezone(UTC).isoformat()

    def _initialize_sync(self) -> None:
        """同步创建数据库结构。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS group_states (
                    chat_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    bot_message_id TEXT NOT NULL UNIQUE,
                    copy_id TEXT NOT NULL,
                    theme TEXT NOT NULL,
                    send_mode TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    reaction_count INTEGER NOT NULL DEFAULT 0,
                    replied INTEGER NOT NULL DEFAULT 0,
                    retort_sent INTEGER NOT NULL DEFAULT 0,
                    shadow INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_observations (
                    event_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    anonymous_sender TEXT NOT NULL,
                    message_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    is_new_turn INTEGER NOT NULL,
                    classification_category TEXT NOT NULL,
                    classification_reason TEXT NOT NULL,
                    signal_name TEXT,
                    decision_reason TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    should_send INTEGER NOT NULL,
                    energy REAL NOT NULL,
                    threshold REAL NOT NULL,
                    probability REAL NOT NULL,
                    roll REAL,
                    energy_added REAL NOT NULL,
                    afternoon_senders INTEGER NOT NULL,
                    afternoon_turns INTEGER NOT NULL,
                    trigger_count INTEGER NOT NULL,
                    time_fallback_count INTEGER NOT NULL,
                    copy_id TEXT,
                    configuration_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_processed_events_created_at
                    ON processed_events(created_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_sent_at
                    ON feedback(sent_at);
                CREATE INDEX IF NOT EXISTS idx_shadow_observations_chat_created
                    ON shadow_observations(chat_id, created_at);
                """
            )

    def _load_state_sync(self, chat_id: str) -> GroupState | None:
        """同步读取群状态。"""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM group_states WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if row is None:
            return None
        return GroupState.from_dict(json.loads(row["state_json"]))

    def _save_state_sync(self, state: GroupState) -> None:
        """同步写入群状态。"""

        payload = json.dumps(state.to_dict(), ensure_ascii=False, separators=(",", ":"))
        updated_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO group_states(chat_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (state.chat_id, payload, updated_at),
            )

    def _claim_event_sync(self, event_id: str, created_at: datetime) -> bool:
        """同步领取幂等事件。"""

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO processed_events(event_id, created_at, claimed_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        event_id,
                        self._utc_isoformat(created_at),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def _release_event_sync(self, event_id: str) -> None:
        """同步释放处理失败的事件。"""

        with self._connect() as connection:
            connection.execute("DELETE FROM processed_events WHERE event_id = ?", (event_id,))

    def _cleanup_events_sync(self, boundary: datetime) -> int:
        """同步清理过期幂等记录。"""

        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM processed_events WHERE created_at < ?",
                (self._utc_isoformat(boundary),),
            )
        return cursor.rowcount

    def _record_shadow_observation_sync(
        self, observation: ShadowObservation, retention_days: int
    ) -> None:
        """同步写入影子观察并按消息时间自动清理过期记录。"""

        boundary = observation.created_at - timedelta(days=retention_days)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO shadow_observations(
                    event_id, chat_id, anonymous_sender, message_text, created_at,
                    is_new_turn, classification_category, classification_reason,
                    signal_name, decision_reason, trigger_source, should_send,
                    energy, threshold, probability, roll, energy_added,
                    afternoon_senders, afternoon_turns, trigger_count,
                    time_fallback_count, copy_id, configuration_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.event_id,
                    observation.chat_id,
                    observation.anonymous_sender,
                    observation.message_text,
                    self._utc_isoformat(observation.created_at),
                    int(observation.is_new_turn),
                    observation.classification_category.value,
                    observation.classification_reason,
                    observation.signal_name,
                    observation.decision_reason,
                    observation.trigger_source.value,
                    int(observation.should_send),
                    observation.energy,
                    observation.threshold,
                    observation.probability,
                    observation.roll,
                    observation.energy_added,
                    observation.afternoon_senders,
                    observation.afternoon_turns,
                    observation.trigger_count,
                    observation.time_fallback_count,
                    observation.copy_id,
                    observation.configuration_version,
                ),
            )
            connection.execute(
                "DELETE FROM shadow_observations WHERE created_at < ?",
                (self._utc_isoformat(boundary),),
            )

    def _list_shadow_observations_sync(
        self, chat_id: str, since: datetime | None
    ) -> list[ShadowObservation]:
        """同步读取并恢复影子观察对象。"""

        query = "SELECT * FROM shadow_observations WHERE chat_id = ?"
        parameters: list[str] = [chat_id]
        if since is not None:
            query += " AND created_at >= ?"
            parameters.append(self._utc_isoformat(since))
        query += " ORDER BY created_at, event_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            ShadowObservation(
                event_id=row["event_id"],
                chat_id=row["chat_id"],
                anonymous_sender=row["anonymous_sender"],
                message_text=row["message_text"],
                created_at=datetime.fromisoformat(row["created_at"]),
                is_new_turn=bool(row["is_new_turn"]),
                classification_category=SignalCategory(row["classification_category"]),
                classification_reason=row["classification_reason"],
                signal_name=row["signal_name"],
                decision_reason=row["decision_reason"],
                trigger_source=TriggerSource(row["trigger_source"]),
                should_send=bool(row["should_send"]),
                energy=float(row["energy"]),
                threshold=float(row["threshold"]),
                probability=float(row["probability"]),
                roll=float(row["roll"]) if row["roll"] is not None else None,
                energy_added=float(row["energy_added"]),
                afternoon_senders=int(row["afternoon_senders"]),
                afternoon_turns=int(row["afternoon_turns"]),
                trigger_count=int(row["trigger_count"]),
                time_fallback_count=int(row["time_fallback_count"]),
                copy_id=row["copy_id"],
                configuration_version=row["configuration_version"],
            )
            for row in rows
        ]

    def _record_feedback_sync(
        self,
        chat_id: str,
        bot_message_id: str,
        copy_id: str,
        theme: str,
        send_mode: SendMode,
        sent_at: datetime,
        shadow: bool,
    ) -> None:
        """同步写入匿名互动记录。"""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO feedback(
                    chat_id, bot_message_id, copy_id, theme, send_mode, sent_at, shadow
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    bot_message_id,
                    copy_id,
                    theme,
                    send_mode.value,
                    self._utc_isoformat(sent_at),
                    int(shadow),
                ),
            )

    def _mark_feedback_reply_sync(self, bot_message_id: str, retort_sent: bool) -> None:
        """同步标记回复和回嘴结果。"""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE feedback
                SET replied = 1, retort_sent = MAX(retort_sent, ?)
                WHERE bot_message_id = ?
                """,
                (int(retort_sent), bot_message_id),
            )

    def _adjust_reaction_sync(self, bot_message_id: str, delta: int) -> None:
        """同步调整表情回应总数且保证不小于零。"""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE feedback
                SET reaction_count = MAX(0, reaction_count + ?)
                WHERE bot_message_id = ?
                """,
                (delta, bot_message_id),
            )

    def _get_metadata_sync(self, key: str) -> str | None:
        """同步读取运行元数据。"""

        with self._connect() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def _set_metadata_sync(self, key: str, value: str) -> None:
        """同步写入运行元数据。"""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metadata(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
