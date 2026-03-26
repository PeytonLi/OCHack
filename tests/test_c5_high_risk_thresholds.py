"""C5: Friendli drafts must satisfy the runnable package contract."""
import pytest
from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


class MissingDescriptionDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {"name": capability, "skill_md": "# broken"}


class MissingSkillMdDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {"name": capability, "description": "broken"}


class ValidDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True

    async def generate_draft(self, capability, context):
        return {
            "name": capability,
            "description": "valid",
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
            json={"capability": "draft-skill", "input_data": {}, "agent_id": "a1"},
        )


@pytest.mark.asyncio
async def test_generated_skill_requires_description():
    response = await _post(MissingDescriptionDetector())
    data = response.json()
    assert data["success"] is False
    assert "description" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_generated_skill_requires_skill_md():
    response = await _post(MissingSkillMdDetector())
    data = response.json()
    assert data["success"] is False
    assert "skill.md" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_valid_generated_skill_executes():
    response = await _post(ValidDetector())
    data = response.json()
    assert data["success"] is True
    assert data["result"] == {"output": "ok"}
