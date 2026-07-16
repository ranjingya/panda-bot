"""盼达机器人进程入口。"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime

from lark_channel import FeishuChannel, SecurityConfig

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


def configure_logging(level: str) -> None:
    """配置全进程标准日志格式。"""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def run() -> None:
    """装配全部依赖并运行飞书长连接。

    参数：
        无。

    返回值：
        长连接停止后正常返回。
    """

    runtime = RuntimeSettings.from_env()
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


def main() -> None:
    """同步命令行入口。"""

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("收到中断信号，进程已退出")


if __name__ == "__main__":
    main()
