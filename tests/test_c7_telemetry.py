"""C7: Telemetry for mean-time-to-capability, block rate, quarantine count, cache-hit ratio.

Behavior under test:
  - Each resolution updates telemetry counters.
  - GET /metrics returns current telemetry state.
  - Metrics include: total_requests, cache_hits, blocks, quarantines, resolutions.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


# ---------- Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {"name": capability, "code": "pass", "version": "0.1.0"}


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class HitSkillRegistry:
    async def search(self, capability: str):
        return {"name": capability, "code": "pass", "version": "1.0"}


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": "docs"}]


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "ok"}

    async def confidence_score(self, skill):
        return 0.95


class ApprovingTrustVerifier:
    async def verify(self, skill) -> bool:
        return True


class RejectingTrustVerifier:
    async def verify(self, skill) -> bool:
        return False


class CacheWithHit:
    def __init__(self):
        self.store = {}

    async def get(self, capability: str):
        return self.store.get(capability)

    async def set(self, capability, resolution, ttl=300):
        self.store[capability] = resolution


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_metrics_endpoint_returns_counters():
    """GET /metrics returns telemetry counters after requests."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Make a successful synthesis request
        await client.post("/resolve-skill-and-run", json={
            "capability": "skill-a", "input_data": {}, "agent_id": "a1",
        })

        metrics_resp = await client.get("/metrics")

    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert metrics["total_requests"] >= 1
    assert "cache_hits" in metrics
    assert "blocks" in metrics
    assert "quarantines" in metrics
    assert "resolutions" in metrics


@pytest.mark.asyncio
async def test_metrics_tracks_blocks():
    """Civic block increments the blocks counter."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=HitSkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=RejectingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/resolve-skill-and-run", json={
            "capability": "blocked-skill", "input_data": {}, "agent_id": "a1",
        })

        metrics = (await client.get("/metrics")).json()

    assert metrics["blocks"] >= 1


@pytest.mark.asyncio
async def test_metrics_tracks_cache_hits():
    """Second request for same capability increments cache_hits."""
    cache = CacheWithHit()
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=cache,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/resolve-skill-and-run", json={
            "capability": "cached-skill", "input_data": {}, "agent_id": "a1",
        })
        await client.post("/resolve-skill-and-run", json={
            "capability": "cached-skill", "input_data": {}, "agent_id": "a1",
        })

        metrics = (await client.get("/metrics")).json()

    assert metrics["cache_hits"] >= 1
