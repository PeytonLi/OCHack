"""C3: Transient registry failures retry once before synthesis fallback."""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import ResolutionStrategy


class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "description": f"Run {capability}",
            "skill_md": f"# {capability}",
            "files": {"SKILL.md": f"# {capability}"},
        }


class TransientFailRegistry:
    def __init__(self):
        self.call_count = 0

    async def search(self, capability: str):
        self.call_count += 1
        if self.call_count == 1:
            raise ConnectionError("registry unavailable")
        return {
            "source": "clawhub",
            "slug": capability,
            "name": capability,
        }


class PermanentFailRegistry:
    def __init__(self):
        self.call_count = 0

    async def search(self, capability: str):
        self.call_count += 1
        raise ConnectionError("registry unavailable")


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "clawhub", "content": "docs"}]


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


class PassSandbox:
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return True

    async def execute(self, skill, input_data):
        return {"output": skill.get("name")}

    async def rollback(self, skill):
        pass


@pytest.mark.asyncio
async def test_transient_registry_failure_retries_then_retrieves():
    registry = TransientFailRegistry()
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=registry,
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=PassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={"capability": "test-skill", "input_data": {}, "agent_id": "a1"},
        )

    data = response.json()
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.CLAWHUB_RETRIEVAL.value
    assert registry.call_count == 2


@pytest.mark.asyncio
async def test_permanent_registry_failure_falls_back_to_synthesis():
    registry = PermanentFailRegistry()
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=registry,
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=PassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={"capability": "test-skill", "input_data": {}, "agent_id": "a1"},
        )

    data = response.json()
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value
    assert registry.call_count == 2
