"""Task 1 / Task 2 独立模型设置，持久化到本机 QSettings。"""
from __future__ import annotations

from dataclasses import dataclass
import os

from PySide6.QtCore import QSettings

from .providers import Capability

ORG = "IELTSExaminer"
APP = "WritingGrader"
QWEN_REVIEW_PROVIDER = "qwen_deepseek_review"
QWEN_DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
TASK1_QWEN_DEFAULT_MODEL = "qwen3.7plus"
TASK2_QWEN_DEFAULT_MODEL = "qwen3.7max"
DEEPSEEK_REVIEW_MODEL = "deepseekv4pro"

# P0-02 keeps existing persisted QSettings keys stable while presenting the
# runtime with provider-neutral route names.
ROUTE_CONFIGURATION_ROLES = {
    "primary": "qwen",
    "syntax_enhancement": "deepseek",
}
TEXT_ROUTE_CAPABILITIES = frozenset(Capability) - frozenset({Capability.IMAGE_INPUT})
VISION_ROUTE_CAPABILITIES = frozenset(Capability)


@dataclass
class ModelConfig:
    model: str
    api_key: str
    base_url: str
    declared_capabilities: frozenset[Capability] = TEXT_ROUTE_CAPABILITIES


@dataclass
class AppSettings:
    task1_qwen_model: str = TASK1_QWEN_DEFAULT_MODEL
    task1_qwen_api_key: str = ""
    task1_qwen_base_url: str = QWEN_DEFAULT_BASE_URL
    task1_deepseek_model: str = DEEPSEEK_REVIEW_MODEL
    task1_deepseek_api_key: str = ""
    task1_deepseek_base_url: str = DEEPSEEK_DEFAULT_BASE_URL
    task2_qwen_model: str = TASK2_QWEN_DEFAULT_MODEL
    task2_qwen_api_key: str = ""
    task2_qwen_base_url: str = QWEN_DEFAULT_BASE_URL
    task2_deepseek_model: str = DEEPSEEK_REVIEW_MODEL
    task2_deepseek_api_key: str = ""
    task2_deepseek_base_url: str = DEEPSEEK_DEFAULT_BASE_URL

    @staticmethod
    def load() -> "AppSettings":
        s = QSettings(ORG, APP)
        legacy_qwen_key = str(s.value(f"api_key/{QWEN_REVIEW_PROVIDER}", ""))
        legacy_qwen_base = str(s.value(f"base_url/{QWEN_REVIEW_PROVIDER}", "")) or QWEN_DEFAULT_BASE_URL
        legacy_ds_key = str(s.value(f"review_api_key/{QWEN_REVIEW_PROVIDER}", ""))
        legacy_ds_base = str(s.value(f"review_base_url/{QWEN_REVIEW_PROVIDER}", "")) or DEEPSEEK_DEFAULT_BASE_URL

        def value(key: str, default: str) -> str:
            return str(s.value(key, default))

        def configured(task: str, role: str, field_name: str, saved: str) -> str:
            task_env = f"{task.upper()}_{role.upper()}_{field_name.upper()}"
            generic_env = f"{role.upper()}_{field_name.upper()}"
            return os.getenv(task_env) or os.getenv(generic_env) or saved

        return AppSettings(
            task1_qwen_model=configured(
                "task1", "qwen", "model",
                value("task1/qwen/model", TASK1_QWEN_DEFAULT_MODEL),
            ),
            task1_qwen_api_key=configured(
                "task1", "qwen", "api_key", value("task1/qwen/api_key", legacy_qwen_key)
            ),
            task1_qwen_base_url=configured(
                "task1", "qwen", "base_url", value("task1/qwen/base_url", legacy_qwen_base)
            ),
            task1_deepseek_model=configured(
                "task1", "deepseek", "model",
                value("task1/deepseek/model", DEEPSEEK_REVIEW_MODEL),
            ),
            task1_deepseek_api_key=configured(
                "task1", "deepseek", "api_key",
                value("task1/deepseek/api_key", legacy_ds_key),
            ),
            task1_deepseek_base_url=configured(
                "task1", "deepseek", "base_url",
                value("task1/deepseek/base_url", legacy_ds_base),
            ),
            task2_qwen_model=configured(
                "task2", "qwen", "model",
                value("task2/qwen/model", TASK2_QWEN_DEFAULT_MODEL),
            ),
            task2_qwen_api_key=configured(
                "task2", "qwen", "api_key", value("task2/qwen/api_key", legacy_qwen_key)
            ),
            task2_qwen_base_url=configured(
                "task2", "qwen", "base_url", value("task2/qwen/base_url", legacy_qwen_base)
            ),
            task2_deepseek_model=configured(
                "task2", "deepseek", "model",
                value("task2/deepseek/model", DEEPSEEK_REVIEW_MODEL),
            ),
            task2_deepseek_api_key=configured(
                "task2", "deepseek", "api_key",
                value("task2/deepseek/api_key", legacy_ds_key),
            ),
            task2_deepseek_base_url=configured(
                "task2", "deepseek", "base_url",
                value("task2/deepseek/base_url", legacy_ds_base),
            ),
        )

    def save(self) -> None:
        s = QSettings(ORG, APP)
        for task in ("task1", "task2"):
            for role in ("qwen", "deepseek"):
                for field_name in ("model", "api_key", "base_url"):
                    s.setValue(
                        f"{task}/{role}/{field_name}",
                        getattr(self, f"{task}_{role}_{field_name}"),
                    )

    def config_for(self, task_type: str, role: str) -> ModelConfig:
        prefix = f"{task_type}_{role}"
        capabilities = (
            VISION_ROUTE_CAPABILITIES
            if task_type == "task1" and role == "qwen"
            else TEXT_ROUTE_CAPABILITIES
        )
        return ModelConfig(
            model=getattr(self, f"{prefix}_model"),
            api_key=getattr(self, f"{prefix}_api_key"),
            base_url=getattr(self, f"{prefix}_base_url"),
            declared_capabilities=capabilities,
        )

    def config_for_route(self, task_type: str, route_key: str) -> ModelConfig:
        """Return legacy persisted settings through a provider-neutral route key."""
        try:
            role = ROUTE_CONFIGURATION_ROLES[route_key]
        except KeyError as exc:
            raise ValueError(f"Unknown provider route key: {route_key}") from exc
        return self.config_for(task_type, role)

    @staticmethod
    def base_url_for(provider: str) -> str:
        return AppSettings.load().task2_qwen_base_url

    @staticmethod
    def review_model_for() -> str:
        return AppSettings.load().task2_deepseek_model

    @staticmethod
    def review_key_for() -> str:
        return AppSettings.load().task2_deepseek_api_key

    @staticmethod
    def review_base_url_for() -> str:
        return AppSettings.load().task2_deepseek_base_url
