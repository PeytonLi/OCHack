"""C6: Dependency licenses must pass a permissive allowlist."""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


class FakeCapabilityDetector:
    def __init__(self, licenses=None):
        self._licenses = licenses or []

    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "description": f"Run {capability}",
            "skill_md": f"# {capability}",
            "files": {"SKILL.md": f"# {capability}"},
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
        return {"output": "ok"}

    async def rollback(self, skill):
        pass


async def _post(detector):
    set_adapters(
        capability_detector=detector,
        skill_registry=EmptySkillRegistry(),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=FakeSkillCache(),
        runtime_sandbox=PassSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/resolve-skill-and-run",
            json={"capability": "safe-skill", "input_data": {}, "agent_id": "a1"},
        )


@pytest.mark.asyncio
async def test_permissive_licenses_allowed():
    response = await _post(
        FakeCapabilityDetector(licenses=["MIT", "Apache-2.0", "BSD-3-Clause"])
    )
    data = response.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_gpl_license_blocked():
    response = await _post(FakeCapabilityDetector(licenses=["MIT", "GPL-3.0"]))
    data = response.json()
    assert data["success"] is False
    assert "license" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_no_dependencies_passes():
    response = await _post(FakeCapabilityDetector(licenses=[]))
    data = response.json()
    assert data["success"] is True
