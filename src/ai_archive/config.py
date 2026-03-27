"""AI Archive — configuration management."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Load .env before anything else
load_dotenv()


class Settings(BaseModel):
    # Application
    app_env: str = Field(default="local")
    data_dir: Path = Field(default=Path("./data"))
    db_file: Path = Field(default=Path("./data/state/archive.db"))

    # Auth
    auth_mode: str = Field(default="attach_cdp")
    chrome_cdp_url: str = Field(default="http://127.0.0.1:9222")
    chrome_user_data_dir: Path = Field(default=Path("./data/state/chrome_profile"))
    chrome_channel: str = Field(default="chrome")
    storage_state_path: Path = Field(default=Path("./data/state/storage_state.json"))
    interactive: bool = Field(default=True)
    login_timeout_seconds: int = Field(default=300)

    # Google Drive
    google_drive_credentials_json: Path = Field(default=Path("./credentials.json"))
    google_drive_token_json: Path = Field(default=Path("./token.json"))
    google_drive_raw_folder_id: str = Field(default="")
    google_drive_curated_folder_id: str = Field(default="")
    drive_enabled: bool = Field(default=False)
    drive_sync_on_crawl: bool = Field(default=False)
    drive_overwrite_if_changed: bool = Field(default=True)

    # Optional credentials (not used by default)
    optional_chatgpt_email: str = Field(default="")
    optional_chatgpt_password: str = Field(default="")
    optional_google_email: str = Field(default="")
    optional_google_password: str = Field(default="")

    # Embeddings
    embedding_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    topic_similarity_threshold: float = Field(default=0.82)

    # Clustering
    clustering_min_cluster_size: int = Field(default=2)
    clustering_algorithm: str = Field(default="hdbscan")

    # Curation / LLM
    curation_llm_provider: str = Field(default="none")
    curation_llm_model: str = Field(default="")
    curation_llm_api_key: str = Field(default="")

    # Crawl
    max_conversations_per_run: int | None = Field(default=None)
    slow_mo_ms: int = Field(default=150)
    jitter_min_ms: int = Field(default=600)
    jitter_max_ms: int = Field(default=1400)
    scroll_attempts: int = Field(default=25)
    scroll_wait_ms: int = Field(default=1500)
    page_timeout_ms: int = Field(default=30000)
    retry_attempts: int = Field(default=3)
    screenshot_on_error: bool = Field(default=True)
    incremental: bool = Field(default=True)

    # Providers
    chatgpt_enabled: bool = Field(default=True)
    chatgpt_base_url: str = Field(default="https://chatgpt.com")
    chatgpt_include_archived: bool = Field(default=True)
    gemini_enabled: bool = Field(default=True)
    gemini_base_url: str = Field(default="https://gemini.google.com")

    # ChatGPT backfill mode
    chatgpt_backfill_enabled: bool = Field(default=False)
    chatgpt_backfill_min_minutes: int = Field(default=45)
    chatgpt_backfill_batch_size: int = Field(default=25)
    chatgpt_backfill_batch_sleep_min_seconds: int = Field(default=5)
    chatgpt_backfill_batch_sleep_max_seconds: int = Field(default=45)
    chatgpt_backfill_scroll_wait_min_ms: int = Field(default=1200)
    chatgpt_backfill_scroll_wait_max_ms: int = Field(default=3000)
    chatgpt_backfill_stagnation_rounds: int = Field(default=8)
    chatgpt_backfill_expected_min_conversations: int = Field(default=500)
    chatgpt_backfill_max_minutes: int = Field(default=90)

    # Logging
    log_level: str = Field(default="INFO")
    json_logs: bool = Field(default=True)
    human_logs: bool = Field(default=True)

    # Archive
    keep_deleted_tombstones: bool = Field(default=True)
    max_snapshot_versions: int = Field(default=10)

    @field_validator("data_dir", "chrome_user_data_dir", "storage_state_path",
                     "google_drive_credentials_json", "google_drive_token_json",
                     "db_file", mode="before")
    @classmethod
    def coerce_path(cls, v: Any) -> Path:
        return Path(v)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def normalized_dir(self) -> Path:
        return self.data_dir / "normalized"

    @property
    def curated_dir(self) -> Path:
        return self.data_dir / "curated"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def config_dir(self) -> Path:
        return Path("./config")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _flatten_yaml(yaml_data: dict[str, Any]) -> dict[str, Any]:
    """Flatten nested YAML settings into flat key=value pairs."""
    flat: dict[str, Any] = {}
    app = yaml_data.get("app", {})
    flat.update({
        "app_env": app.get("env", flat.get("app_env")),
        "data_dir": app.get("data_dir"),
        "db_file": app.get("db_file"),
    })
    auth = yaml_data.get("auth", {})
    flat.update({
        "auth_mode": auth.get("mode"),
        "chrome_cdp_url": auth.get("cdp_url"),
        "chrome_user_data_dir": auth.get("chrome_user_data_dir"),
        "chrome_channel": auth.get("chrome_channel"),
        "storage_state_path": auth.get("storage_state_path"),
        "interactive": auth.get("interactive"),
        "login_timeout_seconds": auth.get("login_timeout_seconds"),
    })
    providers = yaml_data.get("providers", {})
    chatgpt = providers.get("chatgpt", {})
    flat.update({
        "chatgpt_enabled": chatgpt.get("enabled"),
        "chatgpt_base_url": chatgpt.get("base_url"),
        "max_conversations_per_run": chatgpt.get("max_conversations"),
        "chatgpt_include_archived": chatgpt.get("include_archived"),
    })
    gemini = providers.get("gemini", {})
    flat.update({
        "gemini_enabled": gemini.get("enabled"),
        "gemini_base_url": gemini.get("base_url"),
    })
    crawl = yaml_data.get("crawl", {})
    flat.update({
        "incremental": crawl.get("incremental"),
        "slow_mo_ms": crawl.get("slow_mo_ms"),
        "jitter_min_ms": crawl.get("jitter_min_ms"),
        "jitter_max_ms": crawl.get("jitter_max_ms"),
        "scroll_attempts": crawl.get("scroll_attempts"),
        "scroll_wait_ms": crawl.get("scroll_wait_ms"),
        "page_timeout_ms": crawl.get("page_timeout_ms"),
        "retry_attempts": crawl.get("retry_attempts"),
        "screenshot_on_error": crawl.get("screenshot_on_error"),
    })
    clustering = yaml_data.get("clustering", {})
    flat.update({
        "embedding_model": clustering.get("embedding_model"),
        "topic_similarity_threshold": clustering.get("similarity_threshold"),
        "clustering_min_cluster_size": clustering.get("min_cluster_size"),
        "clustering_algorithm": clustering.get("algorithm"),
    })
    curation = yaml_data.get("curation", {})
    flat.update({
        "curation_llm_provider": curation.get("llm_provider"),
        "curation_llm_model": curation.get("llm_model"),
        "curation_llm_api_key": curation.get("llm_api_key"),
    })
    drive = yaml_data.get("drive", {})
    flat.update({
        "drive_enabled": drive.get("enabled"),
        "google_drive_credentials_json": drive.get("credentials_json"),
        "google_drive_token_json": drive.get("token_json"),
        "google_drive_raw_folder_id": drive.get("raw_folder_id"),
        "google_drive_curated_folder_id": drive.get("curated_folder_id"),
        "drive_sync_on_crawl": drive.get("sync_on_crawl"),
        "drive_overwrite_if_changed": drive.get("overwrite_if_changed"),
    })
    logging_cfg = yaml_data.get("logging", {})
    flat.update({
        "log_level": logging_cfg.get("level"),
        "json_logs": logging_cfg.get("json_logs"),
        "human_logs": logging_cfg.get("human_logs"),
    })
    archive = yaml_data.get("archive", {})
    flat.update({
        "keep_deleted_tombstones": archive.get("keep_deleted_tombstones"),
        "max_snapshot_versions": archive.get("max_snapshot_versions"),
    })
    # Remove None values so they don't override defaults
    return {k: v for k, v in flat.items() if v is not None}


def load_settings(yaml_path: Path | None = None) -> Settings:
    """Load settings from YAML + env vars. Env vars take precedence."""
    if yaml_path is None:
        yaml_path = Path("./config/settings.yaml")

    yaml_data = _read_yaml(yaml_path)
    base = _flatten_yaml(yaml_data)

    # Env vars override YAML
    env_map = {
        "APP_ENV": "app_env",
        "AUTH_MODE": "auth_mode",
        "CHROME_CDP_URL": "chrome_cdp_url",
        "CHROME_USER_DATA_DIR": "chrome_user_data_dir",
        "CHROME_CHANNEL": "chrome_channel",
        "STORAGE_STATE_PATH": "storage_state_path",
        "GOOGLE_DRIVE_CREDENTIALS_JSON": "google_drive_credentials_json",
        "GOOGLE_DRIVE_TOKEN_JSON": "google_drive_token_json",
        "GOOGLE_DRIVE_RAW_FOLDER_ID": "google_drive_raw_folder_id",
        "GOOGLE_DRIVE_CURATED_FOLDER_ID": "google_drive_curated_folder_id",
        "OPTIONAL_CHATGPT_EMAIL": "optional_chatgpt_email",
        "OPTIONAL_CHATGPT_PASSWORD": "optional_chatgpt_password",
        "OPTIONAL_GOOGLE_EMAIL": "optional_google_email",
        "OPTIONAL_GOOGLE_PASSWORD": "optional_google_password",
        "EMBEDDING_MODEL": "embedding_model",
        "TOPIC_SIMILARITY_THRESHOLD": "topic_similarity_threshold",
        "CURATION_LLM_PROVIDER": "curation_llm_provider",
        "CURATION_LLM_MODEL": "curation_llm_model",
        "CURATION_LLM_API_KEY": "curation_llm_api_key",
        "MAX_CONVERSATIONS_PER_RUN": "max_conversations_per_run",
        "SLOW_MO_MS": "slow_mo_ms",
        "JITTER_MIN_MS": "jitter_min_ms",
        "JITTER_MAX_MS": "jitter_max_ms",
        "INTERACTIVE": "interactive",
        "CHATGPT_ENABLED": "chatgpt_enabled",
        "GEMINI_ENABLED": "gemini_enabled",
        "CHATGPT_BACKFILL_ENABLED": "chatgpt_backfill_enabled",
        "CHATGPT_BACKFILL_MIN_MINUTES": "chatgpt_backfill_min_minutes",
        "CHATGPT_BACKFILL_BATCH_SIZE": "chatgpt_backfill_batch_size",
        "CHATGPT_BACKFILL_BATCH_SLEEP_MIN_SECONDS": "chatgpt_backfill_batch_sleep_min_seconds",
        "CHATGPT_BACKFILL_BATCH_SLEEP_MAX_SECONDS": "chatgpt_backfill_batch_sleep_max_seconds",
        "CHATGPT_BACKFILL_SCROLL_WAIT_MIN_MS": "chatgpt_backfill_scroll_wait_min_ms",
        "CHATGPT_BACKFILL_SCROLL_WAIT_MAX_MS": "chatgpt_backfill_scroll_wait_max_ms",
        "CHATGPT_BACKFILL_STAGNATION_ROUNDS": "chatgpt_backfill_stagnation_rounds",
        "CHATGPT_BACKFILL_EXPECTED_MIN_CONVERSATIONS": "chatgpt_backfill_expected_min_conversations",
        "CHATGPT_BACKFILL_MAX_MINUTES": "chatgpt_backfill_max_minutes",
    }
    for env_key, field_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None and val != "":
            base[field_key] = val

    return Settings(**base)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings accessor."""
    return load_settings()
