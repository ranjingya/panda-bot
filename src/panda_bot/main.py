"""盼达机器人进程入口。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
from collections.abc import Sequence
from datetime import datetime

from lark_channel import FeishuChannel, PolicyConfig, SecurityConfig

from panda_bot.adapters.feishu import FeishuEventAdapter
from panda_bot.classifier import RuleClassifier
from panda_bot.context import ContextBuffer
from panda_bot.copywriter import Copywriter
from panda_bot.engine import FreedomEngine
from panda_bot.gateway import FeishuMessageGateway
from panda_bot.repository import SQLiteRepository
from panda_bot.service import PandaService
from panda_bot.settings import (
    RuntimeSettings,
    load_message_catalog,
    load_rule_config,
)

logger = logging.getLogger(__name__)


def build_inbound_policy(target_chat_id: str) -> PolicyConfig:
    """构建只接收目标群消息的飞书 SDK 入站策略。

    参数：
        target_chat_id: 允许进入业务适配器的目标群标识。

    返回值：
        无需艾特机器人、且只放行目标群的 SDK 入站策略。
    """

    return PolicyConfig(
        dm_policy="disabled",
        group_policy="allowlist",
        group_allowlist=[target_chat_id],
        require_mention=False,
        respond_to_mention_all=False,
    )


def configure_logging(level: str) -> None:
    """配置全进程标准日志格式。"""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def run(mode: str) -> None:
    """装配全部依赖并运行飞书长连接。

    参数：
        mode: 当前进程的运行模式，只允许 shadow 或 live。

    返回值：
        长连接停止后正常返回。
    """

    runtime = RuntimeSettings.from_env(mode=mode)
    configure_logging(runtime.log_level)
    runtime.validate_credentials()
    rules = load_rule_config(runtime.rules_path)
    catalog = load_message_catalog(runtime.messages_path)

    repository = SQLiteRepository(runtime.database_path)
    await repository.initialize()
    rng = random.SystemRandom()
    copywriter = Copywriter(catalog, rng)
    engine = FreedomEngine(rules, copywriter, rng)
    classifier = RuleClassifier(rules.classifier)
    context = ContextBuffer(rules.context, rules.activity)

    channel = FeishuChannel(
        app_id=runtime.app_id,
        app_secret=runtime.app_secret,
        transport="ws",
        policy=build_inbound_policy(runtime.target_chat_id),
        security=SecurityConfig(mode="audit"),
    )
    gateway = FeishuMessageGateway(channel)
    service = PandaService(
        runtime=runtime,
        repository=repository,
        classifier=classifier,
        context=context,
        engine=engine,
        gateway=gateway,
        clock=lambda: datetime.now(tz=engine.timezone),
    )
    adapter = FeishuEventAdapter(channel, service)
    adapter.register()

    logger.info("盼达启动 mode=%s", runtime.mode)
    await channel.start_background(timeout=30)
    await service.ensure_welcome()
    await repository.cleanup_events(datetime.now(tz=engine.timezone))
    logger.info("飞书长连接已就绪")
    try:
        await asyncio.Event().wait()
    finally:
        logger.info("盼达正在停止")
        await channel.disconnect()


def parse_mode(arguments: Sequence[str] | None = None) -> str:
    """解析必须显式提供的机器人运行模式。

    参数：
        arguments: 可选的命令行参数序列；为空时读取当前进程参数。

    返回值：
        经过 argparse 校验的 shadow 或 live 模式。
    """

    parser = argparse.ArgumentParser(description="启动盼达机器人")
    parser.add_argument("mode", choices=("shadow", "live"), help="机器人运行模式")
    return str(parser.parse_args(arguments).mode)


def main() -> None:
    """解析运行模式并启动机器人长连接。"""

    try:
        asyncio.run(run(parse_mode()))
    except KeyboardInterrupt:
        logger.info("收到中断信号，进程已退出")


if __name__ == "__main__":
    main()
