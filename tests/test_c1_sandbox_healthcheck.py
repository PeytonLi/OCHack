"""C1: Sandbox install/execute with healthcheck and rollback.

Behavior under test:
  After a skill is resolved, it must be installed in a sandbox and pass
  a healthcheck before execution. If healthcheck fails, the skill is
  rolled back and the request fails gracefully.
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


class FakeSkillRegistry:
    def __init__(self, skill=None):
        self._skill = skill

    async def search(self, capability: str):
        return self._skill


class FakeDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": "docs"}]


class FakeGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "ok"}

    async def confidence_score(self, skill):
        return 0.9


class ApprovingTrustVerifier:
    async def verify(self, skill) -> bool:
        return True


class FakeSkillCache:
    async def get(self, capability: str):
        return None

    async def set(self, capability, resolution, ttl=300):
        pass


class HealthySandbox:
    """Sandbox where everything works."""
    def __init__(self):
        self.installed = []
        self.executed = []
        self.rolled_back = []

    async def install(self, skill):
        self.installed.append(skill)
        return True

    async def healthcheck(self, skill):
        return True

    async def execute(self, skill, input_data):
        self.executed.append((skill, input_data))
        return {"output": "sandbox result"}

    async def rollback(self, skill):
        self.rolled_back.append(skill)


class UnhealthySandbox:
    """Sandbox where healthcheck fails — triggers rollback."""
    def __init__(self):
        self.installed = []
        self.rolled_back = []

    async def install(self, skill):
        self.installed.append(skill)
        return True

    async def healthcheck(self, skill):
        return False  # UNHEALTHY

    async def execute(self, skill, input_data):
        raise RuntimeError("should never be called")

    async def rollback(self, skill):
        self.rolled_back.append(skill)


# ---------- Tests ----------

@pytest.mark.asyncio
async def test_sandbox_healthy_executes_and_returns_result():
    """When sandbox healthcheck passes, skill executes and result is returned."""
    sandbox = HealthySandbox()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=FakeSkillRegistry(
            skill={"name": "parse-csv", "code": "pass", "version": "1.0"}
        ),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
        runtime_sandbox=sandbox,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "parse-csv",
            "input_data": {"file": "data.csv"},
            "agent_id": "agent-1",
        })

    data = response.json()

    assert data["success"] is True
    assert len(sandbox.installed) == 1, "Skill should be installed in sandbox"
    assert len(sandbox.executed) == 1, "Skill should be executed in sandbox"
    assert data["result"] == {"output": "sandbox result"}


@pytest.mark.asyncio
async def test_sandbox_unhealthy_triggers_rollback():
    """When sandbox healthcheck fails, skill is rolled back, not executed."""
    sandbox = UnhealthySandbox()

    set_adapters(
        capability_detector=FakeCapabilityDetector(),
        skill_registry=FakeSkillRegistry(
            skill={"name": "parse-csv", "code": "pass", "version": "1.0"}
        ),
        docs_crawler=FakeDocsCrawler(),
        grounding_provider=FakeGroundingProvider(),
        trust_verifier=ApprovingTrustVerifier(),
        skill_cache=FakeSkillCache(),
        runtime_sandbox=sandbox,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/resolve-skill-and-run", json={
            "capability": "parse-csv",
            "input_data": {"file": "data.csv"},
            "agent_id": "agent-1",
        })

    data = response.json()

    assert data["success"] is False
    assert len(sandbox.installed) == 1, "Skill should have been installed"
    assert len(sandbox.rolled_back) == 1, "Skill should be rolled back after failed healthcheck"
    assert "healthcheck" in data.get("error", "").lower() or "sandbox" in data.get("error", "").lower()
