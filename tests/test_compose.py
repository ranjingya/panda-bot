"""Docker Compose 交付配置测试。"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_compose_starts_bot_and_provides_fixed_shadow_export_command() -> None:
    """Compose 应直接启动机器人，并提供无需补充参数的 Shadow 导出服务。"""

    config = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    services = config["services"]

    assert "command" not in services["panda-bot"]
    assert services["panda-bot"]["volumes"] == ["./data:/app/data"]
    assert services["shadow-export"]["profiles"] == ["tools"]
    assert services["shadow-export"]["command"] == [
        "./.venv/bin/panda-shadow-export",
        "--days",
        "5",
        "--output",
        "/app/data/shadow-review.md",
    ]
    assert services["shadow-export"]["volumes"] == ["./data:/app/data"]
