"""C2: Active vs quarantined publish transitions with smoke tests.

Behavior under test:
  - If policy passes AND smoke test passes → publish as ACTIVE.
  - If policy passes BUT smoke test fails → publish as QUARANTINED.
  Smoke test is the sandbox healthcheck on synthesized skills.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import PublishState, ResolutionStrategy


# ---------- Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {"name": capability, "code": "pass", "version": "0.1.0"}


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": "docs"}]


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "ok"}

    async def confidence_score(self, skill):
        return 0.9


class ApprovingTrustVerifier:
    async def verify(self, skill) -> bool:
        return True


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


class SmokeFailSandbox:
    """Install succeeds, healthcheck (smoke test) fails."""
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return False  # smoke test fails

    async def execute(self, skill, input_data):
        raise RuntimeError("should not execute")

    async def rollback(self, skill):
        pass


class SmokePassSandbox:
    """Everything works fine."""
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return True

    async def execute(self, skill, input_data):
        return {"output": "ok"}

    async def rollback(self, skill):
        pass


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_smoke_pass_publishes_as_active():
    """Civic passes + smoke passes → ACTIVE."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
        runtime_sandbox=SmokePassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "good-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True
    assert data["publish_state"] == PublishState.ACTIVE.value
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value


@pytest.mark.asyncio
async def test_smoke_fail_publishes_as_quarantined():
    """Civic passes + smoke fails → QUARANTINED (not a hard failure)."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
        runtime_sandbox=SmokeFailSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "flaky-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()

    # Should NOT be a hard failure — skill is published but quarantined
    assert data["success"] is True, \
        f"Smoke-fail should be partial success (quarantined), got error: {data.get('error')}"
    assert data["publish_state"] == PublishState.QUARANTINED.value
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value
