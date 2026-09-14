"""文档行为测量配置；旧 profile 保留上限，新 profile 不添加单次输出上限。"""

from typing import Literal

from pydantic_ai.models.openai import OpenAIChatModelSettings

MeasurementProfile = Literal["legacy", "calibrated"]
CALIBRATED_PROFILE = "document-policy-live-behavior-v2"


def model_settings(profile: MeasurementProfile) -> OpenAIChatModelSettings:
    if profile not in ("legacy", "calibrated"):
        raise ValueError("unknown measurement profile")
    settings: OpenAIChatModelSettings = {
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    if profile == "legacy":
        settings["max_tokens"] = 2400
    return settings
