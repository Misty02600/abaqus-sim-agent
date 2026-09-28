from pydantic import BaseModel, ConfigDict, Field


class NumericCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    expected: float
    actual: float
    relative_error: float = Field(ge=0)
    tolerance: float = Field(ge=0)
