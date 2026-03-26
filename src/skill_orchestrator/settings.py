from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from skill_orchestrator.exceptions import ConfigurationError


DEFAULT_FRIENDLI_BASE_URL = "https://api.friendli.ai/serverless/v1"
DEFAULT_CONTEXTUAL_BASE_URL = "https://api.contextual.ai/v1"
DEFAULT_CIVIC_BASE_URL = "https://api.civic.example/v1"
DEFAULT_APIFY_BASE_URL = "https://api.apify.com/v2"
DEFAULT_CLAWHUB_BASE_URL = "https://clawhub.ai"


@dataclass(frozen=True)
class Settings:
    friendli_api_key: str
    apify_api_token: str = ""
    contextual_api_key: str = ""
    civic_api_key: str = ""
    redis_url: str = "redis://localhost:6379"
    enable_apify: bool = False
    enable_contextual: bool = False
    enable_civic: bool = False
    enable_redis: bool = False
    friendli_base_url: str = DEFAULT_FRIENDLI_BASE_URL
    friendli_model: str = "meta-llama-3.1-8b-instruct"
    contextual_base_url: str = DEFAULT_CONTEXTUAL_BASE_URL
    contextual_model: str = "contextual-grounded"
    civic_base_url: str = DEFAULT_CIVIC_BASE_URL
    civic_verify_path: str = "/trust/verify"
    clawhub_base_url: str = DEFAULT_CLAWHUB_BASE_URL
    clawhub_search_limit: int = 5
    clawhub_docs_limit: int = 3
    clawhub_min_search_score: float = 1.2
    clawhub_non_suspicious_only: bool = True
    clawhub_skill_file_path: str = "SKILL.md"
    clawhub_tag: str = "latest"
    clawhub_cache_ttl_seconds: int = 3600
    clawhub_bin: str = "clawhub"
    apify_base_url: str = DEFAULT_APIFY_BASE_URL
    apify_docs_actor_id: str = "docs-crawler"
    apify_wait_for_finish_seconds: int = 60
    apify_intended_usage_template: str = (
        "Resolve or synthesize a skill for capability: {capability}."
    )
    apify_improvement_suggestions: str = (
        "Return structured skill metadata and detailed content relevant to the requested capability."
    )
    apify_contact: str = ""
    apify_max_items: int = 25
    apify_download_content: bool = True
    http_timeout_seconds: float = 30.0
    skill_cache_ttl_seconds: int = 300
    sandbox_root: str = str(Path(tempfile.gettempdir()) / "autoskill")
    execution_timeout_seconds: float = 30.0


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    source = _resolved_env(env=env)
    values = {}
    friendli_api_key = source.get("FRIENDLI_API_KEY", "")
    if not _has_real_value(friendli_api_key):
        raise ConfigurationError("Missing required configuration: FRIENDLI_API_KEY")
    values["friendli_api_key"] = friendli_api_key

    values["enable_apify"] = _read_bool(source, "ENABLE_APIFY", False)
    values["enable_contextual"] = _read_bool(source, "ENABLE_CONTEXTUAL", False)
    values["enable_civic"] = _read_bool(source, "ENABLE_CIVIC", False)
    values["enable_redis"] = _read_bool(source, "ENABLE_REDIS", False)

    optional_required = {
        "enable_apify": ("APIFY_API_TOKEN", "apify_api_token"),
        "enable_contextual": ("CONTEXTUAL_API_KEY", "contextual_api_key"),
        "enable_civic": ("CIVIC_API_KEY", "civic_api_key"),
    }
    missing = []
    for flag_name, (env_name, field_name) in optional_required.items():
        value = source.get(env_name, "")
        if values[flag_name]:
            if _has_real_value(value):
                values[field_name] = value
            else:
                missing.append(env_name)
        elif _has_real_value(value):
            values[field_name] = value
        else:
            values[field_name] = ""

    redis_url = source.get("REDIS_URL", "redis://localhost:6379")
    if values["enable_redis"] and not _has_real_value(redis_url):
        missing.append("REDIS_URL")
    values["redis_url"] = redis_url or "redis://localhost:6379"

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
        clawhub_base_url=source.get("CLAWHUB_BASE_URL", DEFAULT_CLAWHUB_BASE_URL),
        clawhub_search_limit=_read_int(source, "CLAWHUB_SEARCH_LIMIT", 5),
        clawhub_docs_limit=_read_int(source, "CLAWHUB_DOCS_LIMIT", 3),
        clawhub_min_search_score=_read_float(
            source, "CLAWHUB_MIN_SEARCH_SCORE", 1.2
        ),
        clawhub_non_suspicious_only=_read_bool(
            source, "CLAWHUB_NON_SUSPICIOUS_ONLY", True
        ),
        clawhub_skill_file_path=source.get("CLAWHUB_SKILL_FILE_PATH", "SKILL.md"),
        clawhub_tag=source.get("CLAWHUB_TAG", "latest"),
        clawhub_cache_ttl_seconds=_read_int(
            source, "CLAWHUB_CACHE_TTL_SECONDS", 3600
        ),
        clawhub_bin=source.get("CLAWHUB_BIN", "clawhub"),
        apify_base_url=source.get("APIFY_BASE_URL", DEFAULT_APIFY_BASE_URL),
        apify_docs_actor_id=source.get("APIFY_DOCS_ACTOR_ID", "docs-crawler"),
        apify_wait_for_finish_seconds=_read_int(
            source, "APIFY_WAIT_FOR_FINISH_SECONDS", 60
        ),
        apify_intended_usage_template=source.get(
            "APIFY_INTENDED_USAGE_TEMPLATE",
            "Resolve or synthesize a skill for capability: {capability}.",
        ),
        apify_improvement_suggestions=source.get(
            "APIFY_IMPROVEMENT_SUGGESTIONS",
            "Return structured skill metadata and detailed content relevant to the requested capability.",
        ),
        apify_contact=source.get("APIFY_CONTACT", ""),
        apify_max_items=_read_int(source, "APIFY_MAX_ITEMS", 25),
        apify_download_content=_read_bool(source, "APIFY_DOWNLOAD_CONTENT", True),
        http_timeout_seconds=_read_float(source, "HTTP_TIMEOUT_SECONDS", 30.0),
        skill_cache_ttl_seconds=_read_int(source, "SKILL_CACHE_TTL_SECONDS", 300),
        sandbox_root=source.get(
            "SANDBOX_ROOT",
            str(Path(tempfile.gettempdir()) / "autoskill"),
        ),
        execution_timeout_seconds=_read_float(
            source, "EXECUTION_TIMEOUT_SECONDS", 30.0
        ),
    )
    return Settings(**values)


def has_required_settings(env: Optional[Mapping[str, str]] = None) -> bool:
    source = _resolved_env(env=env)
    if not _has_real_value(source.get("FRIENDLI_API_KEY")):
        return False
    if _read_bool(source, "ENABLE_APIFY", False) and not _has_real_value(
        source.get("APIFY_API_TOKEN")
    ):
        return False
    if _read_bool(source, "ENABLE_CONTEXTUAL", False) and not _has_real_value(
        source.get("CONTEXTUAL_API_KEY")
    ):
        return False
    if _read_bool(source, "ENABLE_CIVIC", False) and not _has_real_value(
        source.get("CIVIC_API_KEY")
    ):
        return False
    if _read_bool(source, "ENABLE_REDIS", False) and not _has_real_value(
        source.get("REDIS_URL")
    ):
        return False
    return True


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


def _read_bool(source: Mapping[str, str], key: str, default: bool) -> bool:
    raw = source.get(key)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{key} must be a boolean")


def _resolved_env(env: Optional[Mapping[str, str]] = None) -> Mapping[str, str]:
    if env is None:
        resolved = load_dotenv()
        resolved.update(os.environ)
    else:
        resolved = dict(env)
    return {key: _strip_quotes(str(value)) for key, value in resolved.items()}


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
