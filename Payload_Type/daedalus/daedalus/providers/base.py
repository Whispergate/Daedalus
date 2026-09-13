from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class BuildStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        return self in (BuildStatus.SUCCESS, BuildStatus.FAILURE, BuildStatus.CANCELLED)


@dataclass
class BuildResult:
    provider: str
    build_id: str
    status: BuildStatus
    url: str = ""
    duration_seconds: float = 0.0
    artifact_urls: list[str] = field(default_factory=list)
    logs_tail: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)


class CIProvider(ABC):
    name: str

    @abstractmethod
    async def trigger_build(
        self,
        job: str,
        parameters: dict[str, str],
    ) -> BuildResult:
        ...

    @abstractmethod
    async def get_build_status(self, job: str, build_id: str) -> BuildResult:
        ...

    @abstractmethod
    async def download_artifact(self, job: str, build_id: str, artifact_name: str) -> bytes:
        ...

    @abstractmethod
    async def list_jobs(self) -> list[dict]:
        ...

    @abstractmethod
    async def get_build_log(self, job: str, build_id: str, tail: int = 100) -> str:
        ...
