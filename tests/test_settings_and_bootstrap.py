import httpx
import pytest

from skill_orchestrator.adapters.production import (
    ApifyDocsCrawler,
    CivicTrustVerifier,
    ContextualGroundingProvider,
    FriendliCapabilityDetector,
    NullSkillRegistry,
    RedisSkillCache,
)
from skill_orchestrator.app import create_app
from skill_orchestrator.exceptions import ConfigurationError
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
    settings = load_settings(
        {
            "FRIENDLI_API_KEY": "friendli-key",
            "APIFY_API_TOKEN": "apify-key",
            "CONTEXTUAL_API_KEY": "contextual-key",
            "CIVIC_API_KEY": "civic-key",
            "REDIS_URL": "redis://localhost:6379",
        }
    )

    assert settings.friendli_base_url == "https://api.friendli.ai/serverless/v1"
    assert settings.apify_base_url == "https://api.apify.com/v2"
    assert settings.apify_wait_for_finish_seconds == 60
    assert settings.http_timeout_seconds == 30.0


def test_load_dotenv_parses_simple_key_value_file(tmp_path):
    dotenv_path = tmp_path / ".env"
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


def test_load_settings_env_overrides_dotenv_placeholders(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        'FRIENDLI_API_KEY="..."\n'
        'APIFY_API_TOKEN="..."\n'
        'CONTEXTUAL_API_KEY="..."\n'
        'CIVIC_API_KEY="..."\n'
        'REDIS_URL="redis://localhost:6379"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings(
        {
            "FRIENDLI_API_KEY": "friendli-key",
            "APIFY_API_TOKEN": "apify-key",
            "CONTEXTUAL_API_KEY": "contextual-key",
            "CIVIC_API_KEY": "civic-key",
            "REDIS_URL": "redis://cache:6379",
        }
    )

    assert settings.friendli_api_key == "friendli-key"
    assert settings.redis_url == "redis://cache:6379"


def test_create_app_wires_production_adapters():
    settings = load_settings(
        {
            "FRIENDLI_API_KEY": "friendli-key",
            "APIFY_API_TOKEN": "apify-key",
            "CONTEXTUAL_API_KEY": "contextual-key",
            "CIVIC_API_KEY": "civic-key",
            "REDIS_URL": "redis://localhost:6379",
        }
    )

    app = create_app(
        settings,
        transports={
            "friendli": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"choices": []})
            ),
            "apify": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": []})
            ),
            "contextual": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"response": "{}"})
            ),
            "civic": httpx.MockTransport(
                lambda request: httpx.Response(200, json={"trusted": True})
            ),
        },
        redis_client=FakeRedisClient(),
    )

    router = app.state.router
    assert isinstance(router.detector, FriendliCapabilityDetector)
    assert isinstance(router.registry, NullSkillRegistry)
    assert isinstance(router.docs_crawler, ApifyDocsCrawler)
    assert isinstance(router.grounding, ContextualGroundingProvider)
    assert isinstance(router.trust, CivicTrustVerifier)
    assert isinstance(router.cache, RedisSkillCache)
