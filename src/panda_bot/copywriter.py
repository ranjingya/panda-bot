"""审核文案的随机选择与近期去重。"""

from __future__ import annotations

import random
from collections.abc import Sequence

from panda_bot.domain import CopyChoice
from panda_bot.settings import MessageCatalog, MessageTemplate


class Copywriter:
    """从固定审核文案库中选择主动文案和回嘴文案。"""

    def __init__(self, catalog: MessageCatalog, rng: random.Random) -> None:
        self._catalog = catalog
        self._rng = rng

    def choose_proactive(self, recent_ids: Sequence[str]) -> CopyChoice:
        """选择近期没有使用过的主动文案。"""

        recent = set(recent_ids)
        candidates = [item for item in self._catalog.proactive if item.id not in recent]
        if not candidates:
            candidates = self._catalog.proactive
        return self._to_choice(self._rng.choice(candidates))

    def choose_retort(self) -> CopyChoice:
        """随机选择一条互动回嘴文案。"""

        return self._to_choice(self._rng.choice(self._catalog.retorts))

    @staticmethod
    def _to_choice(template: MessageTemplate) -> CopyChoice:
        """将配置模板转换为领域文案对象。"""

        return CopyChoice(template.id, template.theme, template.text)
