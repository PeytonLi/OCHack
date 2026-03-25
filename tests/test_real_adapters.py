import json

import httpx
import pytest

from skill_orchestrator.adapters.production import (
    ApifyDocsCrawler,
    CivicTrustVerifier,
    ContextualGroundingProvider,
    FriendliCapabilityDetector,
    RedisSkillCache,
)
from skill_orchestrator.exceptions import ProviderResponseError, TransientProviderError


class FakeRedisClient:
    def __init__(self):
        self.values = {}
        self.set_calls = []

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.set_calls.append((key, ttl, value))
        self.values[key] = value

    async def aclose(self):
        return None


class BrokenRedisClient:
    async def get(self, key):
        raise RuntimeError("redis unavailable")

    async def setex(self, key, ttl, value):
        raise RuntimeError("redis unavailable")


@pytest.mark.asyncio
async def test_friendli_detector_and_draft_generation_use_json_chat_contract():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "path": request.url.path,
                "auth": request.headers["Authorization"],
                "json": json.loads(request.content.decode()),
            }
        )
        if len(seen) == 1:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"unknown": true}'}}]},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"draft": {"name": "summarize-pdf", "code": "pass", '
                                '"version": "0.1.0"}}'
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        base_url="https://api.friendli.ai/serverless/v1",
        headers={"Authorization": "Bearer friendli-key"},
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = FriendliCapabilityDetector(client=client, model="friendli-model")

        assert await adapter.detect_gap("summarize-pdf") is True
        draft = await adapter.generate_draft("summarize-pdf", {"schema": {}})

    assert draft["name"] == "summarize-pdf"
    assert seen[0]["path"] == "/serverless/v1/chat/completions"
    assert seen[0]["auth"] == "Bearer friendli-key"
    assert seen[0]["json"]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_contextual_grounding_extracts_schema_and_confidence():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode()))
        if len(seen) == 1:
            return httpx.Response(200, json={"response": '{"fields": ["title"]}'})
        return httpx.Response(200, json={"output_text": '{"confidence": 0.91}'})

    async with httpx.AsyncClient(
        base_url="https://api.contextual.ai/v1",
        headers={"Authorization": "Bearer contextual-key"},
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ContextualGroundingProvider(client=client, model="contextual-model")
        schema = await adapter.extract_schema([{"content": "Doc text"}])
        confidence = await adapter.confidence_score({"name": "summarize-pdf"})

    assert schema == {"fields": ["title"]}
    assert confidence == 0.91
    assert seen[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_civic_verifier_maps_boolean_trust_result():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if payload["skill"]["name"] == "safe-skill":
            return httpx.Response(200, json={"trusted": True})
        return httpx.Response(200, json={"trusted": False})

    async with httpx.AsyncClient(
        base_url="https://api.civic.example/v1",
        headers={"Authorization": "Bearer civic-key"},
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = CivicTrustVerifier(client=client)
        assert await adapter.verify({"name": "safe-skill"}) is True
        assert await adapter.verify({"name": "unsafe-skill"}) is False


@pytest.mark.asyncio
async def test_apify_crawler_runs_actor_then_fetches_dataset_items():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.params))
        return httpx.Response(
            200,
            json=[{"source": "apify", "content": "Documentation"}],
        )

    async with httpx.AsyncClient(
        base_url="https://api.apify.com/v2",
        headers={"Authorization": "Bearer apify-key"},
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ApifyDocsCrawler(
            client=client,
            actor_id="docs-crawler",
            wait_for_finish_seconds=45,
        )
        docs = await adapter.crawl_docs("summarize-pdf")

    assert docs == [{"source": "apify", "content": "Documentation"}]
    assert seen[0][1] == "/v2/acts/docs-crawler/run-sync-get-dataset-items"
    assert seen[0][2]["waitForFinish"] == "45"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_http_adapters_raise_transient_errors_for_retryable_status_codes():
    async with httpx.AsyncClient(
        base_url="https://api.civic.example/v1",
        headers={"Authorization": "Bearer civic-key"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, json={"error": "busy"})
        ),
    ) as client:
        adapter = CivicTrustVerifier(client=client)
        with pytest.raises(TransientProviderError):
            await adapter.verify({"name": "safe-skill"})


@pytest.mark.asyncio
async def test_contextual_rejects_malformed_model_output():
    async with httpx.AsyncClient(
        base_url="https://api.contextual.ai/v1",
        headers={"Authorization": "Bearer contextual-key"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"response": "not json"})
        ),
    ) as client:
        adapter = ContextualGroundingProvider(client=client, model="contextual-model")
        with pytest.raises(ProviderResponseError):
            await adapter.extract_schema([{"content": "Doc text"}])


@pytest.mark.asyncio
async def test_redis_cache_round_trip_and_error_mapping():
    cache = RedisSkillCache(FakeRedisClient())
    await cache.set("summarize-pdf", {"result": {"output": "ok"}}, ttl=120)
    cached = await cache.get("summarize-pdf")
    assert cached == {"result": {"output": "ok"}}

    broken_cache = RedisSkillCache(BrokenRedisClient())
    with pytest.raises(TransientProviderError):
        await broken_cache.get("summarize-pdf")
