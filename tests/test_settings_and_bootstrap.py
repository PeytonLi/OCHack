import httpx
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from skill_orchestrator.adapters.production import (
    ApifyDocsCrawler,
    ClawHubCliSandbox,
    ClawHubDocsCrawler,
    ClawHubSkillRegistry,
    FallbackDocsCrawler,
    FriendliCapabilityDetector,
    InMemorySkillCache,
    RedisPayloadCache,
    RedisSkillCache,
)
from skill_orchestrator.app import create_app
from skill_orchestrator.exceptions import ConfigurationError, RuntimeSandboxError
from skill_orchestrator.settings import has_required_settings, load_dotenv, load_settings


class FakeRedisClient:
    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        return True

    async def aclose(self):
        return None


def test_load_settings_requires_provider_configuration():
    with pytest.raises(ConfigurationError):
        load_settings({})


def test_load_settings_applies_defaults():
    settings = load_settings({"FRIENDLI_API_KEY": "friendli-key"})

    assert settings.friendli_base_url == "https://api.friendli.ai/serverless/v1"
    assert settings.clawhub_base_url == "https://clawhub.ai"
    assert settings.clawhub_search_limit == 5
    assert settings.clawhub_docs_limit == 3
    assert settings.clawhub_min_search_score == 1.2
    assert settings.clawhub_non_suspicious_only is True
    assert settings.clawhub_skill_file_path == "SKILL.md"
    assert settings.clawhub_tag == "latest"
    assert settings.clawhub_cache_ttl_seconds == 3600
    assert settings.clawhub_bin == "clawhub"
    assert settings.enable_apify is False
    assert settings.enable_contextual is False
    assert settings.enable_civic is False
    assert settings.enable_redis is False
    assert settings.skill_cache_ttl_seconds == 300
    assert settings.execution_timeout_seconds == 30.0
    assert "autoskill" in settings.sandbox_root


def test_load_dotenv_parses_simple_key_value_file():
    temp_dir = Path(".tmp") / f"dotenv-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        dotenv_path = temp_dir / ".env"
        dotenv_path.write_text(
            'FRIENDLI_API_KEY="friendli-key"\n'
            "APIFY_API_TOKEN=apify-key\n"
            "# comment\n"
            "export REDIS_URL=redis://localhost:6379\n",
            encoding="utf-8",
        )

        values = load_dotenv(dotenv_path)

        assert values["FRIENDLI_API_KEY"] == "friendli-key"
        assert values["APIFY_API_TOKEN"] == "apify-key"
        assert values["REDIS_URL"] == "redis://localhost:6379"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_has_required_settings_rejects_placeholder_values():
    assert has_required_settings(
        {
            "FRIENDLI_API_KEY": "...",
            "APIFY_API_TOKEN": "...",
            "CONTEXTUAL_API_KEY": "...",
            "CIVIC_API_KEY": "...",
            "REDIS_URL": "redis://localhost:6379",
        }
    ) is False


def test_load_settings_env_overrides_dotenv_placeholders(monkeypatch):
    temp_dir = Path(".tmp") / f"dotenv-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        dotenv_path = temp_dir / ".env"
        dotenv_path.write_text(
            'FRIENDLI_API_KEY="..."\n'
            'REDIS_URL="redis://localhost:6379"\n',
            encoding="utf-8",
        )
        monkeypatch.chdir(temp_dir)

        settings = load_settings(
            {
                "FRIENDLI_API_KEY": "friendli-key",
                "REDIS_URL": "redis://cache:6379",
            }
        )

        assert settings.friendli_api_key == "friendli-key"
        assert settings.redis_url == "redis://cache:6379"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_load_settings_requires_enabled_optional_provider_keys():
    with pytest.raises(ConfigurationError):
        load_settings(
            {
                "FRIENDLI_API_KEY": "friendli-key",
                "ENABLE_APIFY": "true",
            }
        )


def test_create_app_wires_production_adapters():
    settings = load_settings(
        {
            "FRIENDLI_API_KEY": "friendli-key",
            "APIFY_API_TOKEN": "apify-key",
            "REDIS_URL": "redis://localhost:6379",
            "ENABLE_APIFY": "true",
            "ENABLE_REDIS": "true",
        }
    )

    app = create_app(
        settings,
        transports={
            "friendli": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"choices": []})
            ),
            "clawhub": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"results": []})
            ),
            "apify": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": []})
            ),
        },
        redis_client=FakeRedisClient(),
    )

    router = app.state.router
    assert isinstance(router.detector, FriendliCapabilityDetector)
    assert isinstance(router.registry, ClawHubSkillRegistry)
    assert isinstance(router.docs_crawler, FallbackDocsCrawler)
    assert isinstance(router.docs_crawler.crawlers[0], ClawHubDocsCrawler)
    assert isinstance(router.docs_crawler.crawlers[1], ApifyDocsCrawler)
    assert isinstance(router.registry.payload_cache, RedisPayloadCache)
    assert isinstance(router.docs_crawler.crawlers[0].payload_cache, RedisPayloadCache)
    assert router.grounding is None
    assert router.trust is None
    assert isinstance(router.cache, RedisSkillCache)
    assert isinstance(router.sandbox, ClawHubCliSandbox)


def test_create_app_uses_clawhub_defaults_when_optional_providers_disabled():
    settings = load_settings({"FRIENDLI_API_KEY": "friendli-key"})
    app = create_app(
        settings,
        transports={
            "clawhub": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"results": []})
            )
        },
    )

    router = app.state.router
    assert isinstance(router.detector, FriendliCapabilityDetector)
    assert isinstance(router.registry, ClawHubSkillRegistry)
    assert isinstance(router.docs_crawler, FallbackDocsCrawler)
    assert isinstance(router.docs_crawler.crawlers[0], ClawHubDocsCrawler)
    assert router.grounding is None
    assert router.trust is None
    assert isinstance(router.cache, InMemorySkillCache)
    assert isinstance(router.sandbox, ClawHubCliSandbox)


def test_runtime_sandbox_fails_fast_when_clawhub_binary_missing():
    with pytest.raises(RuntimeSandboxError):
        ClawHubCliSandbox(
            clawhub_bin="definitely-missing-clawhub-bin",
            sandbox_root="sandbox",
            which=lambda _: None,
        ).validate_configuration()
