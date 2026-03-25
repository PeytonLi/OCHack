"""C6: Dependency licenses must pass a permissive allowlist.

Behavior under test:
  - Skills with permissive licenses (MIT, Apache-2.0, BSD) are allowed.
  - Skills with non-permissive licenses (GPL, AGPL) are blocked.
  - License check happens before publish in synthesis pipeline.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


# ---------- Fakes ----------

class FakeCapabilityDetector:
    def __init__(self, licenses=None):
        self._licenses = licenses or []

    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "code": "pass",
            "version": "0.1.0",
            "dependencies": [
                {"name": f"dep-{i}", "license": lic}
                for i, lic in enumerate(self._licenses)
            ],
        }


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
        return 0.95


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
async def test_permissive_licenses_allowed():
    """Skills with MIT/Apache/BSD licenses pass the allowlist."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(licenses=["MIT", "Apache-2.0", "BSD-3-Clause"]),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "safe-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True, f"Permissive licenses should pass, got: {data.get('error')}"


@pytest.mark.asyncio
async def test_gpl_license_blocked():
    """Skills with GPL dependencies are blocked."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(licenses=["MIT", "GPL-3.0"]),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "gpl-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is False, "GPL dependency should block publication"
    assert "license" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_no_dependencies_passes():
    """Skills with no dependencies pass license check."""
    set_adapters(
        capability_detector=FakeCapabilityDetector(licenses=[]),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "nodeps-skill",
            "input_data": {},
            "agent_id": "a1",
        })

    data = response.json()
    assert data["success"] is True
