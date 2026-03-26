import json

import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from skill_orchestrator.app import create_app
from skill_orchestrator.adapters import production
from skill_orchestrator.models import ResolutionStrategy
from skill_orchestrator.settings import Settings


class FakeRedisClient:
    def __init__(self):
        self.values = {}
        self.get_calls = 0
        self.set_calls = 0
        self.set_keys = []

    async def get(self, key):
        self.get_calls += 1
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.set_calls += 1
        self.set_keys.append(key)
        self.values[key] = value

    async def aclose(self):
        return None


def _settings() -> Settings:
    return Settings(
        friendli_api_key="friendli-key",
        apify_api_token="apify-key",
        redis_url="redis://localhost:6379",
        enable_apify=True,
        enable_redis=True,
        sandbox_root=".autoskill-tests",
    )


def _draft_payload(name: str, output: str) -> str:
    return json.dumps(
        {
            "draft": {
                "name": name,
                "description": f"Run {name}",
                "skill_md": f"# {name}\n\nRun {name}.",
                "files": {
                    "SKILL.md": f"# {name}\n\nRun {name}.",
                    "hooks/run-hook.cmd": f'@echo {{"output":"{output}"}}',
                },
                "dependencies": [],
            }
        }
    )


@pytest.mark.asyncio
async def test_create_app_runs_synthesis_then_cache_hit(monkeypatch):
    friendli_calls = []
    clawhub_calls = []
    apify_calls = []
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
            json={"choices": [{"message": {"content": _draft_payload("summarize-pdf", "ok")}}]},
        )

    def clawhub_handler(request: Request) -> Response:
        clawhub_calls.append((request.method, request.url.path))
        if request.url.path == "/api/v1/skills/summarize-pdf":
            return Response(404, json={"error": "not found"})
        if request.url.path == "/api/v1/search":
            return Response(200, json={"results": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    def apify_handler(request: Request) -> Response:
        apify_calls.append((request.method, request.url.path))
        return Response(200, json=[{"source": "apify", "content": "Docs for summarize-pdf"}])

    async def fake_run_subprocess(command, **kwargs):
        return ('{"output":"ok"}', "")

    monkeypatch.setattr(production, "_run_subprocess", fake_run_subprocess)

    app = create_app(
        _settings(),
        transports={
            "friendli": MockTransport(friendli_handler),
            "clawhub": MockTransport(clawhub_handler),
            "apify": MockTransport(apify_handler),
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
    assert first["publish_state"] is None
    assert first["result"] == {"output": "ok"}
    assert second["success"] is True
    assert second["resolution_strategy"] == ResolutionStrategy.LOCAL_CACHE.value
    assert second["result"] == {"output": "ok"}
    assert len(friendli_calls) == 3
    assert len(clawhub_calls) == 2
    assert len(apify_calls) == 1
    assert any(key.startswith("skill-resolution:") for key in redis_client.set_keys)
    assert any(
        key.startswith("clawhub-download:clawhub:detail:")
        for key in redis_client.set_keys
    )
    assert any(
        key.startswith("clawhub-download:clawhub:search:")
        for key in redis_client.set_keys
    )


@pytest.mark.asyncio
async def test_create_app_returns_native_capability_when_friendli_says_known(monkeypatch):
    def friendli_handler(request: Request) -> Response:
        return Response(
            200,
            json={"choices": [{"message": {"content": '{"unknown": false}'}}]},
        )

    async def fake_run_subprocess(command, **kwargs):
        return ("", "")

    monkeypatch.setattr(production, "_run_subprocess", fake_run_subprocess)

    app = create_app(
        Settings(friendli_api_key="friendli-key", sandbox_root=".autoskill-tests"),
        transports={
            "friendli": MockTransport(friendli_handler),
            "clawhub": MockTransport(lambda request: Response(200, json={"results": []})),
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={
                "capability": "native-shell",
                "input_data": {},
                "agent_id": "agent-2",
            },
        )

    data = response.json()
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.NATIVE_CAPABILITY.value
    assert data["result"]["status"] == ResolutionStrategy.NATIVE_CAPABILITY.value
