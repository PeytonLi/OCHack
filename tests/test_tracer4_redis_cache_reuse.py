"""Tracer 4: Second equivalent request reuses Redis cached resolution.

Behavior under test:
  1. First request for an unknown capability triggers full synthesis.
  2. The result is cached in Redis.
  3. Second identical request resolves from cache — no synthesis calls.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import ResolutionStrategy


# ---------- Tracking Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "code": f"def run(): return '{capability} done'",
            "version": "0.1.0",
        }


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class TrackingDocsCrawler:
    def __init__(self):
        self.call_count = 0

    async def crawl_docs(self, capability: str):
        self.call_count += 1
        return [{"source": "apify", "content": f"Docs for {capability}"}]


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "grounded"}

    async def confidence_score(self, skill):
        return 0.9


class ApprovingTrustVerifier:
    async def verify(self, skill) -> bool:
        return True


class InMemorySkillCache:
    """Simulates Redis — tracks get/set calls."""
    def __init__(self):
        self.store = {}
        self.get_calls = 0
        self.set_calls = 0

    async def get(self, capability: str):
        self.get_calls += 1
        return self.store.get(capability)

    async def set(self, capability: str, resolution, ttl=300):
        self.set_calls += 1
        self.store[capability] = resolution


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_second_request_hits_cache():
    """After synthesis succeeds, a second identical request must come from cache."""
    docs_crawler = TrackingDocsCrawler()
    cache = InMemorySkillCache()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=docs_crawler,
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=cache,
    )

    payload = {
        "capability": "translate-text",
        "input_data": {"text": "hello", "target": "es"},
        "agent_id": "agent-1",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First request — triggers full synthesis
        r1 = await client.post("/resolve-skill-and-run", json=payload)
        d1 = r1.json()

        assert d1["success"] is True
        assert d1["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value
        assert docs_crawler.call_count == 1, "First request should crawl docs once"
        assert cache.set_calls == 1, "Result should be cached after synthesis"

        # Second request — must come from cache, no new synthesis
        r2 = await client.post("/resolve-skill-and-run", json=payload)
        d2 = r2.json()

    assert d2["success"] is True
    assert d2["resolution_strategy"] == ResolutionStrategy.LOCAL_CACHE.value, \
        f"Second request should resolve from cache, got {d2['resolution_strategy']}"
    assert docs_crawler.call_count == 1, \
        "Docs crawler should NOT be called again on cache hit"


@pytest.mark.asyncio
async def test_different_capability_not_cached():
    """A request for a different capability should NOT hit another's cache."""
    docs_crawler = TrackingDocsCrawler()
    cache = InMemorySkillCache()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=docs_crawler,
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=cache,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/resolve-skill-and-run", json={
            "capability": "cap-a", "input_data": {}, "agent_id": "a1",
        })
        await client.post("/resolve-skill-and-run", json={
            "capability": "cap-b", "input_data": {}, "agent_id": "a1",
        })

    # Both should have triggered synthesis (2 crawls, 2 cache sets)
    assert docs_crawler.call_count == 2
    assert cache.set_calls == 2
