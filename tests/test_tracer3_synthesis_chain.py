"""Tracer 3: Synthesis uses ClawHub docs and executes the generated draft."""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters
from skill_orchestrator.models import ResolutionStrategy


class FakeCapabilityDetector:
    def __init__(self):
        self.generate_calls = []

    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        self.generate_calls.append((capability, context))
        return {
            "name": capability,
            "description": f"Run {capability}",
            "skill_md": f"# {capability}",
            "files": {"SKILL.md": f"# {capability}"},
            "dependencies": [],
        }


class EmptySkillRegistry:
    async def search(self, capability: str):
        return None


class TrackingDocsCrawler:
    def __init__(self):
        self.crawl_calls = []

    async def crawl_docs(self, capability: str):
        self.crawl_calls.append(capability)
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
        return {"output": "synthed"}

    async def rollback(self, skill):
        pass


@pytest.mark.asyncio
async def test_synthesis_chain_uses_docs_then_executes():
    detector = FakeCapabilityDetector()
    docs_crawler = TrackingDocsCrawler()

    set_adapters(
        capability_detector=detector,
        skill_registry=EmptySkillRegistry(),
        docs_crawler=docs_crawler,
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=PassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={
                "capability": "extract-invoice-data",
                "input_data": {"file": "invoice.pdf"},
                "agent_id": "agent-2",
            },
        )

    data = response.json()
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value
    assert docs_crawler.crawl_calls == ["extract-invoice-data"]
    assert detector.generate_calls[0][1]["docs"] == [{"source": "clawhub", "content": "docs"}]
