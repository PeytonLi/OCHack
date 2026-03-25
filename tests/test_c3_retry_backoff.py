"""C3: One automatic retry with backoff for transient verify/network failures.

Behavior under test:
  - If Civic verify raises a transient error on first call, retry once.
  - If retry succeeds, proceed normally.
  - If retry also fails, return error.
  - Registry search transient failure also retries once.
"""
import time
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


# ---------- Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {"name": capability, "code": "pass", "version": "0.1.0"}


class FakeSkillRegistry:
    async def search(self, capability: str):
        return {"name": capability, "code": "pass", "version": "1.0"}


class TransientFailRegistry:
    """Fails once then succeeds."""
    def __init__(self):
        self.call_count = 0

    async def search(self, capability: str):
        self.call_count += 1
        if self.call_count == 1:
            raise ConnectionError("transient network failure")
        return {"name": capability, "code": "pass", "version": "1.0"}


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": "docs"}]


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "ok"}

    async def confidence_score(self, skill):
        return 0.9


class TransientFailTrustVerifier:
    """Raises on first call, succeeds on second."""
    def __init__(self):
        self.call_count = 0

    async def verify(self, skill) -> bool:
        self.call_count += 1
        if self.call_count == 1:
            raise ConnectionError("transient Civic API failure")
        return True


class PermanentFailTrustVerifier:
    """Always raises."""
    def __init__(self):
        self.call_count = 0

    async def verify(self, skill) -> bool:
        self.call_count += 1
        raise ConnectionError("permanent Civic API failure")


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_transient_civic_failure_retries_and_succeeds():
    """Civic fails once with transient error, retries, second call succeeds."""
    verifier = TransientFailTrustVerifier()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=FakeSkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=verifier,
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "test-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True, f"Should succeed after retry, got: {data.get('error')}"
    assert verifier.call_count == 2, "Should have called verify exactly twice (1 fail + 1 retry)"


@pytest.mark.asyncio
async def test_permanent_civic_failure_fails_after_retry():
    """Civic fails on both attempts — returns error after one retry."""
    verifier = PermanentFailTrustVerifier()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=FakeSkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=verifier,
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "test-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is False
    assert verifier.call_count == 2, "Should have tried exactly twice"
    assert "transient" in data.get("error", "").lower() or "retry" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_transient_registry_failure_retries():
    """Registry search fails once, retries, second call succeeds."""
    registry = TransientFailRegistry()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=registry,
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=TransientFailTrustVerifier(),  # also transient to test both
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "test-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True, f"Should succeed after retry, got: {data.get('error')}"
    assert registry.call_count == 2
