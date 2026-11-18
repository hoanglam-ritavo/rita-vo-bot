"""Shared strict HTTP DTO base without domain dependencies."""
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


