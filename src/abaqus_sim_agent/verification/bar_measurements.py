"""独立交互试点使用的ODB读数schema；没有从零Gold判分入口。"""

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BarTension3DMeasurements(_FrozenModel):
    odb_path: str = Field(min_length=1)
    step_name: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    instance_name: str = Field(min_length=1)
    element_type_counts: dict[str, int]
    node_count: int = Field(gt=0)
    axis_spans: tuple[float, float, float]
    coordinate_layer_counts: tuple[int, int, int]
    right_mean_u1: float
    mean_s11: float
    left_sum_rf1: float
    y_face_mean_u2_delta: float
    z_face_mean_u3_delta: float
