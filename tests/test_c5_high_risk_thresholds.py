"""C5: High-risk skill categories require stricter policy thresholds.

Behavior under test:
  - Normal skills: confidence >= 0.7 passes.
  - High-risk skills (shell, network, filesystem): confidence >= 0.9 required.
  - A high-risk skill with 0.85 confidence should be blocked.
  - A normal skill with 0.85 confidence should pass.
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


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": "docs"}]


class ConfigurableGroundingProvider:
    def __init__(self, score: float):
        self._score = score

    async def extract_schema(self, raw_docs):
        return {"schema": "ok"}

    async def confidence_score(self, skill):
        return self._score


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
async def test_high_risk_skill_blocked_below_strict_threshold():
    """A shell-related skill with 0.85 confidence should be blocked (needs >= 0.9)."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=ConfigurableGroundingProvider(score=0.85),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "execute-shell-command",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is False, \
        "High-risk skill with 0.85 confidence should be blocked"
    assert "confidence" in data.get("error", "").lower() or "threshold" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_normal_skill_passes_with_moderate_confidence():
    """A normal skill with 0.85 confidence should pass (needs >= 0.7)."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=ConfigurableGroundingProvider(score=0.85),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "parse-csv-file",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True, \
        f"Normal skill with 0.85 confidence should pass, got: {data.get('error')}"


@pytest.mark.asyncio
async def test_high_risk_skill_passes_above_strict_threshold():
    """A high-risk skill with 0.95 confidence should pass."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=ConfigurableGroundingProvider(score=0.95),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "run-network-scan",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True, \
        f"High-risk skill with 0.95 confidence should pass, got: {data.get('error')}"
