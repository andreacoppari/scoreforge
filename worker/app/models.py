from enum import Enum
from typing import Literal

from pydantic import BaseModel


class Instrument(str, Enum):
    piano = "piano"
    guitar = "guitar"
    trumpet = "trumpet"
    violin = "violin"
    flute = "flute"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class Artifact(BaseModel):
    name: str
    label: str
    format: str


class JobResponse(BaseModel):
    id: str
    kind: Literal["transcribe", "separate"]
    status: JobStatus
    progress: int
    message: str
    engine: str | None = None
    artifacts: list[Artifact] = []
    error: str | None = None
