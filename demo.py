"""Demo script: deterministic scenarios for the current AutoSkill flow.

Run with: python demo.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


class DemoCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return capability != "native-capability"

    async def generate_draft(self, capability, context):
        if "fail-draft" in capability:
            return None
        return {
            "name": capability,
            "description": f"Runnable demo skill for {capability}",
            "skill_md": f"# {capability}\n\nDemo skill.",
            "files": {"SKILL.md": f"# {capability}\n\nDemo skill."},
            "dependencies": [{"name": "requests", "license": "Apache-2.0"}],
        }


class DemoSkillRegistry:
    async def search(self, capability: str):
        if capability.startswith("retrieval-"):
            return {"source": "clawhub", "slug": capability, "name": capability}
        return None


class DemoDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "clawhub", "content": f"Documentation for {capability}"}]


class DemoSkillCache:
    def __init__(self):
        self.store = {}

    async def get(self, capability: str):
        return self.store.get(capability)

    async def set(self, capability, resolution, ttl=300):
        self.store[capability] = resolution


class DemoSandbox:
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return "flaky" not in skill.get("name", "")

    async def execute(self, skill, input_data):
        return {"output": f"Executed {skill['name']}", "input": input_data}

    async def rollback(self, skill):
        pass


SCENARIOS = [
    {
        "title": "1. Native Capability",
        "desc": "Friendli says the capability is already available locally",
        "payload": {"capability": "native-capability", "input_data": {}, "agent_id": "demo-1"},
    },
    {
        "title": "2. Retrieval Success",
        "desc": "Capability found in ClawHub and executed through the runtime sandbox",
        "payload": {"capability": "retrieval-parse-csv", "input_data": {"file": "data.csv"}, "agent_id": "demo-1"},
    },
    {
        "title": "3. Synthesis Success",
        "desc": "No retrieval hit, so Friendli generates a runnable draft package",
        "payload": {"capability": "summarize-pdf", "input_data": {"url": "doc.pdf"}, "agent_id": "demo-1"},
    },
    {
        "title": "4. Redis Reuse",
        "desc": "Second request for summarize-pdf resolves from cache",
        "payload": {"capability": "summarize-pdf", "input_data": {"url": "doc.pdf"}, "agent_id": "demo-2"},
    },
    {
        "title": "5. Runtime Failure",
        "desc": "Synthesis succeeds but the sandbox healthcheck fails",
        "payload": {"capability": "flaky-service", "input_data": {}, "agent_id": "demo-1"},
    },
    {
        "title": "6. Partial Success",
        "desc": "Draft generation fails and returns a capability-gap report",
        "payload": {"capability": "fail-draft-impossible", "input_data": {}, "agent_id": "demo-1"},
    },
]


async def main():
    cache = DemoSkillCache()

    set_adapters(
        capability_detector=DemoCapabilityDetector(),
        skill_registry=DemoSkillRegistry(),
        docs_crawler=DemoDocsCrawler(),
        grounding_provider=None,
        trust_verifier=None,
        skill_cache=cache,
        runtime_sandbox=DemoSandbox(),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for scenario in SCENARIOS:
            print(f"\n{'='*60}")
            print(f"  {scenario['title']}")
            print(f"  {scenario['desc']}")
            print(f"{'='*60}")

            resp = await client.post("/resolve-skill-and-run", json=scenario["payload"])
            print(json.dumps(resp.json(), indent=2))

        print(f"\n{'='*60}")
        print("  METRICS")
        print(f"{'='*60}")
        metrics = (await client.get("/metrics")).json()
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
