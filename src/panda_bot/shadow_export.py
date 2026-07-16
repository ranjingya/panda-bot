"""将影子模式校准语料导出为便于 AI 阅读的 Markdown。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from panda_bot.domain import ShadowObservation
from panda_bot.repository import SQLiteRepository
from panda_bot.settings import RuntimeSettings, load_rule_config

logger = logging.getLogger(__name__)


def render_shadow_report(observations: list[ShadowObservation], generated_at: datetime) -> str:
    """把脱敏观察渲染成包含决策快照的 AI 校准报告。

    参数：
        observations: 已按时间排序的影子观察列表。
        generated_at: 报告生成时间。

    返回值：
        可直接交给 AI 阅读的 Markdown 文本。
    """

    category_counts = Counter(item.classification_category.value for item in observations)
    decision_counts = Counter(item.decision_reason for item in observations)
    send_count = sum(item.should_send for item in observations)
    lines = [
        "# 盼达 Shadow 校准记录",
        "",
        f"生成时间：{generated_at.isoformat(timespec='seconds')}",
        f"消息数：{len(observations)}；规则判定本应发送：{send_count}",
        "",
        "## AI 校准任务",
        "",
        "- 从真实表达中找出被判为 ordinary_message、但实际上属于工作收尾的说法。",
        "- 找出当前规则可能误判的完成、否定、返工和事故表达。",
        "- 总结群聊的句长、节奏、常用梗和接话方式，为固定文案与未来 AI 文案提供建议。",
        "- 只分析群体表达，不推断成员身份、性格、绩效或个人画像。",
        "- 建议以可配置短语、上下文规则和文案主题呈现，最终触发权仍由规则引擎负责。",
        "",
        "## 汇总",
        "",
        f"分类：{_format_counts(category_counts)}",
        f"决策原因：{_format_counts(decision_counts)}",
        "",
        "## 按时间排列的脱敏消息与决策",
        "",
    ]
    if not observations:
        lines.append("暂无 Shadow 校准记录。")
        return "\n".join(lines) + "\n"

    for index, item in enumerate(observations, start=1):
        probability = f"{item.probability:.0%}" if item.probability else "-"
        roll = f"{item.roll:.4f}" if item.roll is not None else "-"
        quoted_text = item.message_text.replace("\n", "\n> ") or "[空文本]"
        heading = (
            f"### {index}. {item.created_at.isoformat(timespec='seconds')}"
            f" · 成员-{item.anonymous_sender}"
        )
        classification_line = (
            f"分类 `{item.classification_category.value}` / "
            f"`{item.classification_reason}`；信号 `{item.signal_name or '-'}`；"
            f"决策 `{item.decision_reason}`；来源 `{item.trigger_source.value}`；"
            f"新轮次 `{item.is_new_turn}`"
        )
        state_line = (
            f"能量 `{item.energy:.2f}/{item.threshold:.2f}`；"
            f"新增 `{item.energy_added:.2f}`；概率 `{probability}`；抽签 `{roll}`；"
            f"下午活跃 `{item.afternoon_senders} 人/{item.afternoon_turns} 轮`"
        )
        lines.extend(
            [
                heading,
                "",
                f"> {quoted_text}",
                "",
                classification_line,
                state_line,
                (
                    f"本应发送 `{item.should_send}`；文案 `{item.copy_id or '-'}`；"
                    f"触发前次数 `{item.trigger_count}`；时间兜底 `{item.time_fallback_count}`；"
                    f"规则版本 `{item.configuration_version}`"
                ),
                "",
            ]
        )
    return "\n".join(lines)


def _format_counts(counts: Counter[str]) -> str:
    """将计数器渲染为紧凑的键值列表。"""

    if not counts:
        return "无"
    return "、".join(f"{name}={count}" for name, count in counts.most_common())


async def export_shadow_report(
    *,
    database_path: Path,
    chat_id: str,
    output_path: Path,
    since: datetime,
    generated_at: datetime,
) -> int:
    """从 SQLite 读取 Shadow 语料并写出 Markdown 报告。

    参数：
        database_path: 机器人使用的 SQLite 数据库路径。
        chat_id: 需要导出的目标群标识。
        output_path: Markdown 报告输出路径。
        since: 需要包含的最早消息时间。
        generated_at: 报告生成时间。

    返回值：
        实际写入报告的消息数量。
    """

    repository = SQLiteRepository(database_path)
    await repository.initialize()
    observations = await repository.list_shadow_observations(chat_id, since)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_shadow_report(observations, generated_at), encoding="utf-8")
    logger.info("Shadow 校准报告已生成 path=%s messages=%s", output_path, len(observations))
    return len(observations)


def main() -> None:
    """解析命令行参数并导出最近若干天的 Shadow 校准报告。

    参数：
        无；参数由命令行和进程环境提供。

    返回值：
        无返回值；成功时在指定路径生成 Markdown 文件。
    """

    parser = argparse.ArgumentParser(description="导出盼达 Shadow 模式的脱敏校准记录")
    parser.add_argument("--days", type=int, help="导出最近多少天，默认使用规则配置")
    parser.add_argument("--output", type=Path, default=Path("data/shadow-review.md"))
    parser.add_argument("--chat-id", help="目标群 ID，默认读取 PANDA_TARGET_CHAT_ID")
    arguments = parser.parse_args()

    runtime = RuntimeSettings.from_env()
    rules = load_rule_config(runtime.rules_path)
    chat_id = arguments.chat_id or runtime.target_chat_id
    if not chat_id:
        parser.error("缺少 --chat-id 或 PANDA_TARGET_CHAT_ID")
    days = arguments.days if arguments.days is not None else rules.shadow_collection.retention_days
    if not 1 <= days <= rules.shadow_collection.retention_days:
        parser.error(f"--days 必须位于 1 到 {rules.shadow_collection.retention_days} 之间")

    logging.basicConfig(level=runtime.log_level, format="%(levelname)s %(name)s %(message)s")
    generated_at = datetime.now(ZoneInfo(rules.schedule.timezone))
    asyncio.run(
        export_shadow_report(
            database_path=runtime.database_path,
            chat_id=chat_id,
            output_path=arguments.output,
            since=generated_at - timedelta(days=days),
            generated_at=generated_at,
        )
    )


if __name__ == "__main__":
    main()
