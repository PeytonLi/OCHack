import json

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from skill_orchestrator.app import create_app
from skill_orchestrator.models import PublishState, ResolutionStrategy
from skill_orchestrator.settings import Settings


class FakeRedisClient:
    def __init__(self):
        self.values = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, key):
        self.get_calls += 1
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.set_calls += 1
        self.values[key] = value

    async def aclose(self):
        return None


def _settings() -> Settings:
    return Settings(
        friendli_api_key="friendli-key",
        apify_api_token="apify-key",
        contextual_api_key="contextual-key",
        civic_api_key="civic-key",
        redis_url="redis://localhost:6379",
        enable_apify=True,
        enable_contextual=True,
        enable_civic=True,
        enable_redis=True,
    )


@pytest.mark.asyncio
async def test_create_app_runs_real_adapter_synthesis_then_cache_hit():
    friendli_calls = []
    apify_calls = []
    contextual_calls = []
    civic_calls = []
    redis_client = FakeRedisClient()

    def friendli_handler(request: Request) -> Response:
        friendli_calls.append(json.loads(request.content.decode()))
        if len(friendli_calls) in {1, 3}:
            return Response(
                200,
                json={"choices": [{"message": {"content": '{"unknown": true}'}}]},
            )
        return Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"draft": {"name": "summarize-pdf", "code": "pass", '
                                '"version": "0.1.0", "dependencies": []}}'
                            )
                        }
                    }
                ]
            },
        )

    def apify_handler(request: Request) -> Response:
        apify_calls.append((request.method, request.url.path))
        return Response(
            200,
            json=[{"source": "apify", "content": "Docs for summarize-pdf"}],
        )

    def contextual_handler(request: Request) -> Response:
        contextual_calls.append(json.loads(request.content.decode()))
        if len(contextual_calls) == 1:
            return Response(200, json={"response": '{"fields": ["title"]}'})
        return Response(200, json={"response": '{"confidence": 0.95}'})

    def civic_handler(request: Request) -> Response:
        civic_calls.append(json.loads(request.content.decode()))
        return Response(200, json={"trusted": True})

    app = create_app(
        _settings(),
        transports={
            "friendli": MockTransport(friendli_handler),
            "apify": MockTransport(apify_handler),
            "contextual": MockTransport(contextual_handler),
            "civic": MockTransport(civic_handler),
        },
        redis_client=redis_client,
    )

    payload = {
        "capability": "summarize-pdf",
        "input_data": {"url": "doc.pdf"},
        "agent_id": "agent-1",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.post("/resolve-skill-and-run", json=payload)).json()
        second = (await client.post("/resolve-skill-and-run", json=payload)).json()

    assert first["success"] is True
    assert first["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value
    assert first["publish_state"] == PublishState.ACTIVE.value
    assert second["success"] is True
    assert second["resolution_strategy"] == ResolutionStrategy.LOCAL_CACHE.value
    assert len(friendli_calls) == 3
    assert len(apify_calls) == 1
    assert len(contextual_calls) == 2
    assert len(civic_calls) == 1
    assert redis_client.get_calls == 2
    assert redis_client.set_calls == 1


@pytest.mark.asyncio
async def test_create_app_real_adapters_respect_civic_hard_block():
    redis_client = FakeRedisClient()

    def friendli_handler(request: Request) -> Response:
        body = json.loads(request.content.decode())
        messages = body.get("messages", [])
        if "Determine whether" in messages[0]["content"]:
            return Response(
                200,
                json={"choices": [{"message": {"content": '{"unknown": true}'}}]},
            )
        return Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"draft": {"name": "run-network-scan", "code": "pass", '
                                '"version": "0.1.0", "dependencies": []}}'
                            )
                        }
                    }
                ]
            },
        )

    def apify_handler(request: Request) -> Response:
        return Response(
            200,
            json=[{"source": "apify", "content": "Docs for run-network-scan"}],
        )

    contextual_call_count = {"count": 0}

    def contextual_handler(request: Request) -> Response:
        contextual_call_count["count"] += 1
        if contextual_call_count["count"] == 1:
            return Response(200, json={"response": '{"fields": ["host"]}'})
        return Response(200, json={"response": '{"confidence": 0.95}'})

    def civic_handler(request: Request) -> Response:
        return Response(200, json={"trusted": False})

    app = create_app(
        _settings(),
        transports={
            "friendli": MockTransport(friendli_handler),
            "apify": MockTransport(apify_handler),
            "contextual": MockTransport(contextual_handler),
            "civic": MockTransport(civic_handler),
        },
        redis_client=redis_client,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={
                "capability": "run-network-scan",
                "input_data": {},
                "agent_id": "agent-2",
            },
        )

    data = response.json()
    assert data["success"] is False
    assert "trust" in data["error"].lower() or "blocked" in data["error"].lower()
    assert redis_client.set_calls == 0
