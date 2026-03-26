"""C7: Telemetry tracks requests, resolutions, and cache hits."""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


class FakeCapabilityDetector:
    def __init__(self, unknown=True):
        self.unknown = unknown

    async def detect_gap(self, capability: str) -> bool:
        return self.unknown

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "description": f"Run {capability}",
            "skill_md": f"# {capability}",
            "files": {"SKILL.md": f"# {capability}"},
        }


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "clawhub", "content": "docs"}]


class CacheWithHit:
    def __init__(self):
        self.store = {}

    async def get(self, capability: str):
        return self.store.get(capability)

    async def set(self, capability, resolution, ttl=300):
        self.store[capability] = resolution


class PassSandbox:
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return True

    async def execute(self, skill, input_data):
        return {"output": "ok"}

    async def rollback(self, skill):
        pass


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_counters():
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=CacheWithHit(),
        runtime_sandbox=PassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/resolve-skill-and-run",
            json={"capability": "skill-a", "input_data": {}, "agent_id": "a1"},
        )
        metrics_resp = await client.get("/metrics")

    metrics = metrics_resp.json()
    assert metrics_resp.status_code == 200
    assert metrics["total_requests"] >= 1
    assert metrics["resolutions"] >= 1
    assert "cache_hits" in metrics
    assert "mean_time_to_capability" in metrics


@pytest.mark.asyncio
async def test_metrics_tracks_cache_hits():
    cache = CacheWithHit()
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=cache,
        runtime_sandbox=PassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/resolve-skill-and-run",
            json={"capability": "cached-skill", "input_data": {}, "agent_id": "a1"},
        )
        await client.post(
            "/resolve-skill-and-run",
            json={"capability": "cached-skill", "input_data": {}, "agent_id": "a1"},
        )
        metrics = (await client.get("/metrics")).json()

    assert metrics["cache_hits"] >= 1
