"""Tracer 1: Unknown capability triggers discovery flow.

Behavior under test:
  POST /resolve-skill-and-run with an unknown capability should NOT fail
  immediately or loop. Instead it should route through the discovery engine
  and return a response that indicates discovery was attempted.

  We inject fake adapters so the test is deterministic and fast.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import ResolutionStrategy


# ---------- Fakes ----------

class FakeCapabilityDetector:
    def __init__(self):
        self.detect_gap_calls = []

    async def detect_gap(self, capability: str) -> bool:
        self.detect_gap_calls.append(capability)
        return True  # always unknown

    async def generate_draft(self, capability, context):
        return None


class FakeSkillRegistry:
    async def search(self, capability: str):
        return None  # nothing in ClawHub


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return []


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {}

    async def confidence_score(self, skill):
        return 0.0


class FakeTrustVerifier:
    async def verify(self, skill):
        return True


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability: str, resolution, ttl=300):
        pass


# ---------- Test ----------

@pytest.mark.asyncio
async def test_unknown_capability_triggers_discovery():
    """When an unknown capability is requested, the system enters the
    discovery flow rather than returning an immediate error."""
    detector = FakeCapabilityDetector()

    set_adapters(
        capability_detector=detector,
        skill_registry=FakeSkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=FakeTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "summarize-pdf",
            "input_data": {"url": "https://example.com/doc.pdf"},
            "agent_id": "agent-1",
        })

    data = response.json()

    # The system must have asked the detector whether this capability is known
    assert "summarize-pdf" in detector.detect_gap_calls, \
        "Discovery flow was never entered - detector was not consulted"

    # The response should NOT be a raw 'not implemented' error
    assert data.get("error") != "not implemented", \
        "System returned a stub error instead of entering discovery"

    # When discovery finds nothing (all fakes return empty), we expect a
    # graceful partial-success or gap report - not a 500.
    assert response.status_code == 200
    assert data["capability"] == "summarize-pdf"
