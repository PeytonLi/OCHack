"""C4: Partial success plus capability-gap report when safe resolution fails.

Behavior under test:
  When resolution partially succeeds (e.g., docs were crawled, schema
  extracted, but draft generation fails), the response should include:
  - success=False
  - capability_gaps with attempted strategies
  - partial results from completed steps
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


# ---------- Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return None  # draft generation fails


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class TrackingDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": "docs found"}]


class TrackingGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "grounded", "fields": ["a", "b"]}

    async def confidence_score(self, skill):
        return 0.8


class ApprovingTrustVerifier:
    async def verify(self, skill) -> bool:
        return True


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_partial_success_reports_gap_with_attempted_strategies():
    """When draft generation fails, response includes gap report with strategies tried."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=TrackingDocsCrawler(),
        grounding_provider=TrackingGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "impossible-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()

    assert response.status_code == 200
    assert data["success"] is False
    assert data["capability"] == "impossible-skill"

    # Must have capability gaps
    gaps = data.get("capability_gaps", [])
    assert len(gaps) >= 1, "Should report at least one capability gap"

    gap = gaps[0]
    assert gap["capability"] == "impossible-skill"
    assert "draft" in gap["reason"].lower() or "generation" in gap["reason"].lower()

    # Must list all attempted strategies
    strategies = gap["attempted_strategies"]
    assert "local_cache" in strategies
    assert "clawhub_retrieval" in strategies
    assert "synthesis" in strategies

    # Should include partial results from steps that succeeded
    partial = data.get("result")
    assert partial is not None, \
        "Partial success should include results from completed steps (docs, schema)"
    assert "docs" in partial or "schema" in partial, \
        f"Partial result should include docs or schema data, got: {partial}"
