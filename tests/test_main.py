"""机器人启动参数测试。"""

from __future__ import annotations

import pytest

from panda_bot.main import parse_mode


@pytest.mark.parametrize("mode", ["shadow", "live"])
def test_parse_mode_accepts_explicit_runtime_modes(mode: str) -> None:
    """启动命令必须接受明确的 Shadow 和 Live 参数。"""

    assert parse_mode([mode]) == mode


def test_parse_mode_rejects_missing_mode() -> None:
    """未提供模式时应快速失败，避免误以为机器人处于另一模式。"""

    with pytest.raises(SystemExit):
        parse_mode([])
