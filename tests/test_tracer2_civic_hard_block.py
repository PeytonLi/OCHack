"""Tracer 2: A ClawHub retrieval hit executes through the runtime sandbox."""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import ResolutionStrategy


class FakeCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        raise AssertionError("synthesis should not run when retrieval hits")


class FakeSkillRegistry:
    async def search(self, capability: str):
        return {"source": "clawhub", "slug": capability, "name": capability}


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        raise AssertionError("docs crawler should not be used")


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


class TrackingSandbox:
    def __init__(self):
        self.installed = []
        self.executed = []

    async def install(self, skill):
        self.installed.append(skill)
        return True

    async def healthcheck(self, skill):
        return True

    async def execute(self, skill, input_data):
        self.executed.append((skill, input_data))
        return {"output": "retrieved"}

    async def rollback(self, skill):
        pass


@pytest.mark.asyncio
async def test_retrieval_hit_executes_via_runtime_sandbox():
    sandbox = TrackingSandbox()
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=FakeSkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=sandbox,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={"capability": "send-email", "input_data": {}, "agent_id": "agent-1"},
        )

    data = response.json()
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.CLAWHUB_RETRIEVAL.value
    assert len(sandbox.installed) == 1
    assert len(sandbox.executed) == 1
