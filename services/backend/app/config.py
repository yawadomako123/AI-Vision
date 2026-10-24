from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).resolve()
# Repo checkout: <root>/services/backend/app/config.py. Docker image: /app/app/config.py.
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    postgres_db: str = "aivision"
    postgres_user: str = "aivision"
    postgres_password: str = ""
    # Not "localhost": on Windows it tries IPv6 first and stalls against Docker's IPv4-only port.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    # Overrides the postgres_* fields when set, e.g. sqlite:///storage/aivision.db (no Docker needed).
    database_url: str = ""

    # Fernet key for camera stream URLs (they contain camera passwords).
    camera_secret_key: str
    # Shared secret the vision worker sends in X-Service-Token.
    service_token: str

    event_storage_path: Path = REPO_ROOT / "storage" / "events"
    # SigLIP text model for plain-English search (scripts/export_siglip.py writes it).
    search_model_dir: Path = REPO_ROOT / "models" / "siglip"
    # Built dashboard to serve at "/" (defaults to apps/web/dist in the repo).
    web_dist: Path | None = None
    # Hourly cleanup of old events/snapshots/count samples (disabled in tests).
    run_retention: bool = True
    session_hours: int = 12
    # Set true when the dashboard is served over HTTPS (remote access).
    cookie_secure: bool = False
    # MediaMTX control API + playback server (recording, IP-camera proxies). Empty password = off.
    mediamtx_api_password: str = ""
    mediamtx_api_url: str = "http://127.0.0.1:9997"
    mediamtx_playback_url: str = "http://127.0.0.1:9996"
    # How the vision worker reaches MediaMTX's RTSP server ("mediamtx:8554" inside Docker).
    mediamtx_rtsp: str = "127.0.0.1:8554"
    recordings_path: Path = REPO_ROOT / "storage" / "recordings"
    # Dashboard origins allowed to call the API from the browser.
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @field_validator("event_storage_path", "search_model_dir", "recordings_path")
    @classmethod
    def _repo_relative(cls, p: Path) -> Path:
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def db_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            path = Path(url.removeprefix("sqlite:///"))
            if not path.is_absolute():  # e.g. sqlite:///storage/aivision.db -> <project>/storage/aivision.db
                path = REPO_ROOT / path
                path.parent.mkdir(parents=True, exist_ok=True)
                url = f"sqlite:///{path.as_posix()}"
        return url or (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}?connect_timeout=5"
        )


settings = Settings()
