import json

import httpx
import pytest

from skill_orchestrator.adapters.production import (
    ApifyDocsCrawler,
    CivicTrustVerifier,
    ClawHubDocsCrawler,
    ClawHubSkillRegistry,
    ContextualGroundingProvider,
    FriendliCapabilityDetector,
    RedisPayloadCache,
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
async def test_clawhub_registry_uses_search_then_fetches_skill_markdown():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/api/v1/skills/calendar-sync":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/api/v1/search":
            assert request.url.params["q"] == "calendar sync"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "slug": "calendar",
                            "displayName": "Calendar",
                            "summary": "Manage calendars and meetings.",
                            "version": None,
                            "score": 3.7,
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/skills/calendar":
            return httpx.Response(
                200,
                json={
                    "skill": {
                        "slug": "calendar",
                        "displayName": "Calendar",
                        "summary": "Manage calendars and meetings.",
                        "tags": {"latest": "1.0.0"},
                        "stats": {"downloads": 42},
                    },
                    "latestVersion": {"version": "1.0.0"},
                    "metadata": {"os": ["linux"]},
                    "owner": {"handle": "publisher"},
                    "moderation": None,
                },
            )
        if request.url.path == "/api/v1/skills/calendar/file":
            assert request.url.params["path"] == "SKILL.md"
            assert request.url.params["tag"] == "latest"
            return httpx.Response(200, text="# Calendar\n\nDo calendar tasks.")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://clawhub.ai",
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ClawHubSkillRegistry(client=client)
        skill = await adapter.search("calendar sync")

    assert skill["source"] == "clawhub"
    assert skill["slug"] == "calendar"
    assert skill["name"] == "Calendar"
    assert skill["version"] == "1.0.0"
    assert skill["search_score"] == 3.7
    assert "Do calendar tasks." in skill["skill_md"]
    assert seen[0][1] == "/api/v1/skills/calendar-sync"
    assert seen[1][1] == "/api/v1/search"
    assert seen[2][1] == "/api/v1/skills/calendar"
    assert seen[3][1] == "/api/v1/skills/calendar/file"


@pytest.mark.asyncio
async def test_clawhub_registry_reuses_redis_cached_search_and_skill_downloads():
    seen = []
    redis_client = FakeRedisClient()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/api/v1/skills/calendar-sync":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/api/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "slug": "calendar",
                            "displayName": "Calendar",
                            "summary": "Manage calendars and meetings.",
                            "version": None,
                            "score": 3.7,
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/skills/calendar":
            return httpx.Response(
                200,
                json={
                    "skill": {
                        "slug": "calendar",
                        "displayName": "Calendar",
                        "summary": "Manage calendars and meetings.",
                        "tags": {"latest": "1.0.0"},
                        "stats": {"downloads": 42},
                    },
                    "latestVersion": {"version": "1.0.0"},
                    "metadata": {"os": ["linux"]},
                    "owner": {"handle": "publisher"},
                    "moderation": None,
                },
            )
        if request.url.path == "/api/v1/skills/calendar/file":
            return httpx.Response(200, text="# Calendar\n\nDo calendar tasks.")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://clawhub.ai",
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ClawHubSkillRegistry(
            client=client,
            payload_cache=RedisPayloadCache(
                redis_client,
                namespace="clawhub-download",
            ),
        )
        first = await adapter.search("calendar sync")
        second = await adapter.search("calendar sync")

    assert first == second
    assert len(seen) == 4
    assert seen[0][1] == "/api/v1/skills/calendar-sync"
    assert seen[1][1] == "/api/v1/search"
    assert seen[2][1] == "/api/v1/skills/calendar"
    assert seen[3][1] == "/api/v1/skills/calendar/file"


@pytest.mark.asyncio
async def test_clawhub_registry_rejects_low_score_non_exact_matches():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v1/skills/definitely-not-a-real-skill-xyz123":
            return httpx.Response(404, json={"error": "not found"})
        if request.url.path == "/api/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "slug": "skillscanner",
                            "displayName": "Skillscanner",
                            "summary": "Scan skills.",
                            "version": None,
                            "score": 1.0,
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://clawhub.ai",
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ClawHubSkillRegistry(client=client, min_search_score=1.2)
        skill = await adapter.search("definitely-not-a-real-skill-xyz123")

    assert skill is None
    assert seen == [
        "/api/v1/skills/definitely-not-a-real-skill-xyz123",
        "/api/v1/search",
    ]


@pytest.mark.asyncio
async def test_clawhub_docs_crawler_fetches_skill_markdown_for_top_results():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/api/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "slug": "calendar",
                            "displayName": "Calendar",
                            "summary": "Manage calendars and meetings.",
                            "version": None,
                            "score": 3.7,
                        },
                        {
                            "slug": "noise-skill",
                            "displayName": "Noise Skill",
                            "summary": "Ignore this one.",
                            "version": None,
                            "score": 0.9,
                        },
                    ]
                },
            )
        if request.url.path == "/api/v1/skills/calendar":
            return httpx.Response(
                200,
                json={
                    "skill": {
                        "slug": "calendar",
                        "displayName": "Calendar",
                        "summary": "Manage calendars and meetings.",
                    },
                    "latestVersion": {"version": "1.0.0"},
                },
            )
        if request.url.path == "/api/v1/skills/calendar/file":
            return httpx.Response(200, text="# Calendar\n\nUse this skill.")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://clawhub.ai",
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ClawHubDocsCrawler(client=client, docs_limit=2, min_search_score=1.2)
        docs = await adapter.crawl_docs("calendar")

    assert len(docs) == 1
    assert docs[0]["slug"] == "calendar"
    assert "Summary: Manage calendars and meetings." in docs[0]["content"]
    assert "Use this skill." in docs[0]["content"]
    assert seen[0][0] == "/api/v1/search"
    assert seen[1][0] == "/api/v1/skills/calendar"
    assert seen[2][0] == "/api/v1/skills/calendar/file"


@pytest.mark.asyncio
async def test_clawhub_docs_crawler_reuses_redis_cached_skill_downloads():
    seen = []
    redis_client = FakeRedisClient()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/api/v1/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "slug": "calendar",
                            "displayName": "Calendar",
                            "summary": "Manage calendars and meetings.",
                            "version": None,
                            "score": 3.7,
                        }
                    ]
                },
            )
        if request.url.path == "/api/v1/skills/calendar":
            return httpx.Response(
                200,
                json={
                    "skill": {
                        "slug": "calendar",
                        "displayName": "Calendar",
                        "summary": "Manage calendars and meetings.",
                    },
                    "latestVersion": {"version": "1.0.0"},
                },
            )
        if request.url.path == "/api/v1/skills/calendar/file":
            return httpx.Response(200, text="# Calendar\n\nUse this skill.")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        base_url="https://clawhub.ai",
        transport=httpx.MockTransport(handler),
    ) as client:
        adapter = ClawHubDocsCrawler(
            client=client,
            docs_limit=2,
            min_search_score=1.2,
            payload_cache=RedisPayloadCache(
                redis_client,
                namespace="clawhub-download",
            ),
        )
        first = await adapter.crawl_docs("calendar")
        second = await adapter.crawl_docs("calendar")

    assert first == second
    assert len(seen) == 3
    assert seen[0][0] == "/api/v1/search"
    assert seen[1][0] == "/api/v1/skills/calendar"
    assert seen[2][0] == "/api/v1/skills/calendar/file"


@pytest.mark.asyncio
async def test_apify_crawler_runs_actor_then_fetches_dataset_items():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.url.params,
                json.loads(request.content.decode()),
            )
        )
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
            intended_usage_template="Investigate capability {capability}",
            improvement_suggestions="Return relevant docs",
            contact="ops@example.com",
            max_items=7,
            download_content=False,
        )
        docs = await adapter.crawl_docs("summarize-pdf")

    assert docs == [{"source": "apify", "content": "Documentation"}]
    assert seen[0][1] == "/v2/acts/docs-crawler/run-sync-get-dataset-items"
    assert seen[0][2]["waitForFinish"] == "45"
    assert seen[0][3] == {
        "sp_intended_usage": "Investigate capability summarize-pdf",
        "sp_improvement_suggestions": "Return relevant docs",
        "sp_contact": "ops@example.com",
        "maxItems": 7,
        "downloadContent": False,
    }
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
