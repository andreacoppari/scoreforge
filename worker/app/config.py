from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    data_dir: Path = Path("/data")
    device: str = "cpu"
    transcription_engine: str = "basic-pitch"
    max_upload_mb: int = 80
    max_concurrent_jobs: int = 1
    job_ttl_hours: int = 24
    demucs_model: str = "htdemucs"
    muscriptor_model: str = "large"
    allowed_origins: str = "http://localhost:8080"

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
