"""C2: Mainline synthesis no longer publishes or quarantines."""
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


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "clawhub", "content": "docs"}]


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


class SmokeFailSandbox:
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return False

    async def execute(self, skill, input_data):
        raise RuntimeError("should not execute")

    async def rollback(self, skill):
        pass


class SmokePassSandbox:
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return True

    async def execute(self, skill, input_data):
        return {"output": "ok"}

    async def rollback(self, skill):
        pass


@pytest.mark.asyncio
async def test_smoke_pass_executes_without_publish_state():
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=SmokePassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={"capability": "good-skill", "input_data": {}, "agent_id": "a1"},
        )

    data = response.json()
    assert data["success"] is True
    assert data["publish_state"] is None
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value


@pytest.mark.asyncio
async def test_smoke_fail_returns_runtime_error():
    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=SmokeFailSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={"capability": "flaky-skill", "input_data": {}, "agent_id": "a1"},
        )

    data = response.json()
    assert data["success"] is False
    assert data["publish_state"] is None
    assert "healthcheck" in data.get("error", "").lower()
