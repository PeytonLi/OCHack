"""Tracer 3: No-skill path executes docs-to-draft-to-validate-to-publish chain.

Behavior under test:
  When ClawHub has no match for a capability, the system must:
    1. Crawl docs (Apify)
    2. Extract grounded schema (Contextual AI)
    3. Generate draft skill (Friendli)
    4. Verify trust (Civic) — hard block if fails
    5. Publish the skill
  and return a successful synthesis resolution.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import ResolutionStrategy, PublishState


# ---------- Tracking Fakes ----------

class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True  # always unknown

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "code": f"def run(): return '{capability} executed'",
            "version": "0.1.0",
        }


class EmptySkillRegistry:
    """ClawHub has nothing — forces synthesis path."""
    async def search(self, capability: str):
        return None


class TrackingDocsCrawler:
    def __init__(self):
        self.crawl_calls = []

    async def crawl_docs(self, capability: str):
        self.crawl_calls.append(capability)
        return [{"source": "apify", "content": f"Docs for {capability}"}]


class TrackingGroundingProvider:
    def __init__(self):
        self.extract_calls = []
        self.score_calls = []

    async def extract_schema(self, raw_docs):
        self.extract_calls.append(raw_docs)
        return {"schema": "grounded", "fields": ["input", "output"]}

    async def confidence_score(self, skill):
        self.score_calls.append(skill)
        return 0.92


class TrackingTrustVerifier:
    def __init__(self, approve: bool = True):
        self._approve = approve
        self.verify_calls = []

    async def verify(self, skill) -> bool:
        self.verify_calls.append(skill)
        return self._approve


class FakeSkillCache:
    def __init__(self):
        self.stored = {}

    async def get(self, capability: str):
        return self.stored.get(capability)

    async def set(self, capability: str, resolution, ttl=300):
        self.stored[capability] = resolution


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_synthesis_chain_full_success():
    """When no skill exists, the full synthesis pipeline runs and publishes."""
    docs_crawler = TrackingDocsCrawler()
    grounding = TrackingGroundingProvider()
    trust = TrackingTrustVerifier(approve=True)
    cache = FakeSkillCache()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=docs_crawler,
        grounding_provider=grounding,
        trust_verifier=trust,
        skill_cache=cache,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "extract-invoice-data",
            "input_data": {"file": "invoice.pdf"},
            "agent_id": "agent-2",
        })

    data = response.json()

    # 1. Apify docs crawl must have been called
    assert "extract-invoice-data" in docs_crawler.crawl_calls, \
        "Apify docs crawler was never called"

    # 2. Contextual grounding must have been called
    assert len(grounding.extract_calls) > 0, \
        "Contextual grounding extract_schema was never called"
    assert len(grounding.score_calls) > 0, \
        "Contextual grounding confidence_score was never called"

    # 3. Civic trust must have been called on the draft
    assert len(trust.verify_calls) > 0, \
        "Civic trust verifier was never called on synthesized skill"

    # 4. The response must indicate synthesis strategy
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value

    # 5. The skill should be published as active
    assert data["publish_state"] == PublishState.ACTIVE.value

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_synthesis_chain_civic_blocks_draft():
    """When synthesis produces a draft but Civic rejects it, hard block."""
    trust = TrackingTrustVerifier(approve=False)

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=TrackingDocsCrawler(),
        grounding_provider=TrackingGroundingProvider(),
        trust_verifier=trust,
        skill_cache=FakeSkillCache(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "run-shell-command",
            "input_data": {},
            "agent_id": "agent-3",
        })

    data = response.json()

    assert len(trust.verify_calls) > 0, "Civic was never consulted"
    assert data["success"] is False
    assert "trust" in data.get("error", "").lower() or "blocked" in data.get("error", "").lower()
