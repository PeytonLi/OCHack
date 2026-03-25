from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from skill_orchestrator.exceptions import ConfigurationError


DEFAULT_FRIENDLI_BASE_URL = "https://api.friendli.ai/serverless/v1"
DEFAULT_CONTEXTUAL_BASE_URL = "https://api.contextual.ai/v1"
DEFAULT_CIVIC_BASE_URL = "https://api.civic.example/v1"
DEFAULT_APIFY_BASE_URL = "https://api.apify.com/v2"


@dataclass(frozen=True)
class Settings:
    friendli_api_key: str
    apify_api_token: str
    contextual_api_key: str
    civic_api_key: str
    redis_url: str
    friendli_base_url: str = DEFAULT_FRIENDLI_BASE_URL
    friendli_model: str = "meta-llama-3.1-8b-instruct"
    contextual_base_url: str = DEFAULT_CONTEXTUAL_BASE_URL
    contextual_model: str = "contextual-grounded"
    civic_base_url: str = DEFAULT_CIVIC_BASE_URL
    civic_verify_path: str = "/trust/verify"
    apify_base_url: str = DEFAULT_APIFY_BASE_URL
    apify_docs_actor_id: str = "docs-crawler"
    apify_wait_for_finish_seconds: int = 60
    http_timeout_seconds: float = 30.0


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    source = _resolved_env(env=env)
    required = {
        "FRIENDLI_API_KEY": "friendli_api_key",
        "APIFY_API_TOKEN": "apify_api_token",
        "CONTEXTUAL_API_KEY": "contextual_api_key",
        "CIVIC_API_KEY": "civic_api_key",
        "REDIS_URL": "redis_url",
    }

    values = {}
    missing = []
    for env_name, field_name in required.items():
        value = source.get(env_name)
        if _has_real_value(value):
            values[field_name] = value
        else:
            missing.append(env_name)

    if missing:
        raise ConfigurationError(
            f"Missing required configuration: {', '.join(sorted(missing))}"
        )

    values.update(
        friendli_base_url=source.get("FRIENDLI_BASE_URL", DEFAULT_FRIENDLI_BASE_URL),
        friendli_model=source.get("FRIENDLI_MODEL", "meta-llama-3.1-8b-instruct"),
        contextual_base_url=source.get(
            "CONTEXTUAL_BASE_URL", DEFAULT_CONTEXTUAL_BASE_URL
        ),
        contextual_model=source.get("CONTEXTUAL_MODEL", "contextual-grounded"),
        civic_base_url=source.get("CIVIC_BASE_URL", DEFAULT_CIVIC_BASE_URL),
        civic_verify_path=source.get("CIVIC_VERIFY_PATH", "/trust/verify"),
        apify_base_url=source.get("APIFY_BASE_URL", DEFAULT_APIFY_BASE_URL),
        apify_docs_actor_id=source.get("APIFY_DOCS_ACTOR_ID", "docs-crawler"),
        apify_wait_for_finish_seconds=_read_int(
            source, "APIFY_WAIT_FOR_FINISH_SECONDS", 60
        ),
        http_timeout_seconds=_read_float(source, "HTTP_TIMEOUT_SECONDS", 30.0),
    )
    return Settings(**values)


def has_required_settings(env: Optional[Mapping[str, str]] = None) -> bool:
    source = _resolved_env(env=env)
    return all(
        _has_real_value(source.get(name))
        for name in (
            "FRIENDLI_API_KEY",
            "APIFY_API_TOKEN",
            "CONTEXTUAL_API_KEY",
            "CIVIC_API_KEY",
            "REDIS_URL",
        )
    )


def load_dotenv(path: str | Path = ".env") -> Dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}

    values: Dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        values[key] = _strip_quotes(value)
    return values


def _read_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be an integer") from exc


def _read_float(source: Mapping[str, str], key: str, default: float) -> float:
    raw = source.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} must be a float") from exc


def _resolved_env(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    resolved = load_dotenv()
    if env is None:
        resolved.update(os.environ)
    else:
        resolved.update(env)
    return resolved


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _has_real_value(value: Optional[str]) -> bool:
    if value is None:
        return False
    normalized = value.strip()
    if not normalized:
        return False
    return normalized.lower() not in {
        "...",
        "<fill-me>",
        "<required>",
        "changeme",
        "your-key-here",
    }
