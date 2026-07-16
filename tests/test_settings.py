"""外部配置校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from panda_bot.settings import RuntimeSettings, load_message_catalog, load_rule_config


def test_default_configs_are_valid() -> None:
    """默认规则和文案应通过完整校验。"""

    rules = load_rule_config(Path("config/rules.yaml"))
    catalog = load_message_catalog(Path("config/messages.yaml"))

    assert rules.trigger.daily_limit == 4
    assert rules.schedule.earliest_send == "14:30"
    assert rules.shadow_collection.enabled is True
    assert rules.shadow_collection.retention_days == 5
    assert rules.shadow_collection.max_text_chars == 2000
    assert len(catalog.proactive) == 60
    assert len(catalog.retorts) == 18


def test_runtime_rejects_invalid_mode() -> None:
    """运行模式只能使用影子或正式值。"""

    with pytest.raises(ValueError, match="shadow 或 live"):
        RuntimeSettings(mode="unknown")


def test_runtime_reports_missing_credentials() -> None:
    """缺少飞书配置时应给出明确错误。"""

    runtime = RuntimeSettings()
    with pytest.raises(ValueError, match="LARK_APP_ID"):
        runtime.validate_credentials()


def test_runtime_loads_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """运行配置应从容器或命令行注入的环境变量加载。"""

    monkeypatch.setenv("LARK_APP_ID", "cli_test")
    monkeypatch.setenv("LARK_APP_SECRET", "secret")
    monkeypatch.setenv("PANDA_TARGET_CHAT_ID", "oc_test")
    monkeypatch.setenv("PANDA_DATABASE_PATH", "/app/data/panda.db")

    runtime = RuntimeSettings.from_env()

    assert runtime.app_id == "cli_test"
    assert runtime.app_secret == "secret"
    assert runtime.target_chat_id == "oc_test"
    assert runtime.database_path == Path("/app/data/panda.db")
