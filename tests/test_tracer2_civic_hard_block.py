"""Tracer 2: Civic verification failure is a hard block.

Behavior under test:
  When a skill IS found via ClawHub retrieval, but Civic trust verification
  fails, the system must NOT execute or return the skill. It must return
  a failure response indicating the trust block.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


# ---------- Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True  # unknown, triggers discovery

    async def generate_draft(self, capability, context):
        return None


class FakeSkillRegistry:
    """ClawHub returns a skill — it exists but needs verification."""
    async def search(self, capability: str):
        return {"name": capability, "code": "print('hello')", "version": "1.0"}


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return []


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {}

    async def confidence_score(self, skill):
        return 0.9


class RejectingTrustVerifier:
    """Civic says NO — hard block."""
    def __init__(self):
        self.verify_calls = []

    async def verify(self, skill) -> bool:
        self.verify_calls.append(skill)
        return False  # BLOCKED


class ApprovingTrustVerifier:
    """Civic says YES — allow."""
    def __init__(self):
        self.verify_calls = []

    async def verify(self, skill) -> bool:
        self.verify_calls.append(skill)
        return True


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability: str, resolution, ttl=300):
        pass


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_civic_rejection_blocks_skill_execution():
    """A skill found in ClawHub must be blocked if Civic verification fails."""
    verifier = RejectingTrustVerifier()

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
            "capability": "send-email",
            "input_data": {},
            "agent_id": "agent-1",
        })

    data = response.json()

    # Civic must have been consulted
    assert len(verifier.verify_calls) > 0, "Civic verifier was never called"

    # The skill must NOT be returned as a success
    assert data["success"] is False, "Skill was returned despite Civic rejection"

    # The error must clearly indicate a trust/verification block
    assert "trust" in data.get("error", "").lower() or "blocked" in data.get("error", "").lower(), \
        f"Error should mention trust block, got: {data.get('error')}"

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_civic_approval_allows_skill_through():
    """A skill found in ClawHub proceeds when Civic verification passes."""
    verifier = ApprovingTrustVerifier()

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
            "capability": "send-email",
            "input_data": {},
            "agent_id": "agent-1",
        })

    data = response.json()

    assert len(verifier.verify_calls) > 0, "Civic verifier was never called"
    assert data["success"] is True, "Skill should succeed when Civic approves"
    assert response.status_code == 200
