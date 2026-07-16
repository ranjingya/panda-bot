"""测试共享夹具。"""

from __future__ import annotations

from pathlib import Path

import pytest

from panda_bot.settings import MessageCatalog, RuleConfig, load_message_catalog, load_rule_config


@pytest.fixture
def rules() -> RuleConfig:
    """加载仓库默认规则。"""

    return load_rule_config(Path("config/rules.yaml"))


@pytest.fixture
def catalog() -> MessageCatalog:
    """加载仓库默认文案。"""

    return load_message_catalog(Path("config/messages.yaml"))


class DeterministicRandom:
    """始终选择可预测结果的随机数替身。"""

    @staticmethod
    def uniform(start: float, end: float) -> float:
        """返回区间中点。"""

        return (start + end) / 2

    @staticmethod
    def random() -> float:
        """返回确保概率抽签成功的零值。"""

        return 0.0

    @staticmethod
    def randint(start: int, end: int) -> int:
        """返回整数区间下界。"""

        return start

    @staticmethod
    def choice(items):
        """返回候选集合第一项。"""

        return items[0]


@pytest.fixture
def deterministic_random() -> DeterministicRandom:
    """提供确定性随机数替身。"""

    return DeterministicRandom()
