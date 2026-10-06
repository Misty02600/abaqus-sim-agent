"""仅保存按提供商、模型选择的输出上限；新会话读取，恢复沿用 launch。"""

from pathlib import Path

from filelock import FileLock
from platformdirs import user_data_path
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .persistence import write_bytes_atomic
from .providers import ModelProvider


class ModelOutputSettings(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    max_tokens: int | None = Field(default=None, gt=0)


_records = TypeAdapter(dict[str, dict[str, ModelOutputSettings]])


def model_output_settings_path() -> Path:
    return (
        user_data_path("abaqus-sim-agent", appauthor=False)
        / "model-output-settings.json"
    )


class ModelOutputSettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else model_output_settings_path()

    def read(self) -> dict[str, dict[str, ModelOutputSettings]]:
        try:
            return _records.validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"无法读取模型输出设置 {self.path}，请检查文件；原文件未被覆盖。"
            ) from exc

    def get(self, provider: ModelProvider, name: str) -> ModelOutputSettings:
        return self.read().get(provider.value, {}).get(name, ModelOutputSettings())

    def save(
        self, provider: ModelProvider, name: str, settings: ModelOutputSettings
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=5):
            records = self.read()
            models = records.setdefault(provider.value, {})
            if settings.max_tokens is None:
                models.pop(name, None)
                if not models:
                    records.pop(provider.value, None)
            else:
                models[name] = settings
            write_bytes_atomic(self.path, _records.dump_json(records, indent=2) + b"\n")
