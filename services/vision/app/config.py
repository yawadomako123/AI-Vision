from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # API mode: cameras come from the API and events are sent to it.
    api_base_url: str = ""
    service_token: str = ""
    camera_refresh_seconds: float = 10.0
    # Standalone mode (no API_BASE_URL): comma-separated RTSP URLs or file paths.
    camera_sources: str = "rtsp://localhost:8554/cam1"

    model_path: Path = REPO_ROOT / "models" / "rfdetr-nano.onnx"
    # OpenVINO device: GPU (Intel iGPU), CPU, or AUTO. Falls back to plain CPU if unavailable.
    inference_device: str = "GPU"
    inference_fps: float = 3.0
    detection_confidence: float = 0.4
    # COCO class names to keep; everything else is discarded before tracking.
    detect_classes: str = "person,bicycle,car,motorcycle,bus,truck"

    event_storage_path: Path = REPO_ROOT / "storage" / "events"
    # Short evidence clips around alerts (standalone mode; in API mode the dashboard setting wins).
    clips_enabled: bool = True
    rules_path: Path = REPO_ROOT / "config" / "rules.json"
    # Raise camera_offline only after this long, so brief Wi-Fi blips don't alert.
    offline_alert_seconds: float = 15.0

    @field_validator("model_path", "event_storage_path", "rules_path")
    @classmethod
    def _repo_relative(cls, p: Path) -> Path:
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def sources(self) -> list[str]:
        return [s.strip() for s in self.camera_sources.split(",") if s.strip()]

    @property
    def classes(self) -> set[str]:
        return {c.strip() for c in self.detect_classes.split(",") if c.strip()}


settings = Settings()
