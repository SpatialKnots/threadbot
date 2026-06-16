from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional until dependencies are installed
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("your_") or lowered in {"change_me", "replace_me", "token"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    vk_access_token: str
    vk_group_domain: str
    vk_api_version: str
    database_url: str
    image_storage_path: Path
    results_per_page: int
    startup_fetch_enabled: bool
    startup_fetch_limit: int
    startup_fetch_batch_size: int
    startup_rebuild_search: bool


def get_settings(require_tokens: bool = True) -> Settings:
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    vk_token = os.getenv("VK_ACCESS_TOKEN", "").strip()
    missing = []
    if require_tokens and (not telegram_token or _is_placeholder(telegram_token)):
        missing.append("TELEGRAM_BOT_TOKEN")
    if require_tokens and (not vk_token or _is_placeholder(vk_token)):
        missing.append("VK_ACCESS_TOKEN")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    return Settings(
        telegram_bot_token=telegram_token,
        vk_access_token=vk_token,
        vk_group_domain=os.getenv("VK_GROUP_DOMAIN", "thewebmthread").strip(),
        vk_api_version=os.getenv("VK_API_VERSION", "5.199").strip(),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./threads.db").strip(),
        image_storage_path=Path(os.getenv("IMAGE_STORAGE_PATH", "./data/images")),
        results_per_page=_int_env("RESULTS_PER_PAGE", 5),
        startup_fetch_enabled=_bool_env("STARTUP_FETCH_ENABLED", True),
        startup_fetch_limit=_int_env("STARTUP_FETCH_LIMIT", 100),
        startup_fetch_batch_size=_int_env("STARTUP_FETCH_BATCH_SIZE", 100),
        startup_rebuild_search=_bool_env("STARTUP_REBUILD_SEARCH", True),
    )


def get_optional_token(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None
