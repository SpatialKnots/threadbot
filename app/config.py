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
    )


def get_optional_token(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None
