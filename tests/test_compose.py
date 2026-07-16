"""Docker Compose 交付配置测试。"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_compose_exposes_explicit_shadow_and_live_modes() -> None:
    """Compose 应以服务参数明确选择模式，并提供固定的 Shadow 导出服务。"""

    config = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    services = config["services"]

    assert services["shadow"]["command"] == ["shadow"]
    assert services["shadow"]["profiles"] == ["shadow"]
    assert services["live"]["command"] == ["live"]
    assert services["live"]["profiles"] == ["live"]
    assert services["shadow"]["volumes"] == ["./data:/app/data"]
    assert services["live"]["volumes"] == ["./data:/app/data"]
    assert services["shadow-export"]["profiles"] == ["tools"]
    assert services["shadow-export"]["entrypoint"] == ["./.venv/bin/panda-shadow-export"]
    assert services["shadow-export"]["command"] == [
        "--days",
        "5",
        "--output",
        "/app/data/shadow-review.md",
    ]
    assert services["shadow-export"]["volumes"] == ["./data:/app/data"]
