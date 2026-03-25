"""Demo script: deterministic scenarios for the skill orchestrator.

Run with: python demo.py
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import app, set_adapters


# ── Configurable Fakes ──────────────────────────────────────────────────────

class DemoCapabilityDetector:
    async def detect_gap(self, capability: str) -> bool:
        return True  # all capabilities are "unknown" for demo

    async def generate_draft(self, capability, context):
        if "fail-draft" in capability:
            return None
        return {
            "name": capability,
            "code": f"def run(input): return '{capability} result'",
            "version": "0.1.0",
            "dependencies": [
                {"name": "requests", "license": "Apache-2.0"},
            ],
        }


class DemoSkillRegistry:
    """Returns a skill only for 'retrieval-*' capabilities."""
    async def search(self, capability: str):
        if capability.startswith("retrieval-"):
            return {"name": capability, "code": "pass", "version": "1.0"}
        return None


class DemoDocsCrawler:
    async def crawl_docs(self, capability: str):
        return [{"source": "apify", "content": f"Documentation for {capability}"}]


class DemoGroundingProvider:
    async def extract_schema(self, raw_docs):
        return {"schema": "grounded", "fields": ["input", "output"]}

    async def confidence_score(self, skill):
        return 0.92


class DemoCivicVerifier:
    """Blocks capabilities containing 'unsafe'."""
    async def verify(self, skill) -> bool:
        name = skill.get("name", "")
        return "unsafe" not in name


class DemoSkillCache:
    def __init__(self):
        self.store = {}

    async def get(self, capability: str):
        return self.store.get(capability)

    async def set(self, capability, resolution, ttl=300):
        self.store[capability] = resolution


class DemoSandbox:
    """Fails healthcheck for 'flaky-*' capabilities."""
    async def install(self, skill):
        return True

    async def healthcheck(self, skill):
        return "flaky" not in skill.get("name", "")

    async def execute(self, skill, input_data):
        return {"output": f"Executed {skill['name']}"}

    async def rollback(self, skill):
        pass


# ── Scenarios ────────────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "title": "1. Retrieval Success",
        "desc": "Capability found in ClawHub, Civic approves",
        "payload": {"capability": "retrieval-parse-csv", "input_data": {"file": "data.csv"}, "agent_id": "demo-1"},
    },
    {
        "title": "2. Synthesis Success",
        "desc": "Not in ClawHub, full synthesis pipeline runs",
        "payload": {"capability": "summarize-pdf", "input_data": {"url": "doc.pdf"}, "agent_id": "demo-1"},
    },
    {
        "title": "3. Civic Block",
        "desc": "Capability name contains 'unsafe' -- Civic rejects",
        "payload": {"capability": "retrieval-unsafe-action", "input_data": {}, "agent_id": "demo-1"},
    },
    {
        "title": "4. Redis Reuse",
        "desc": "Second request for 'summarize-pdf' hits cache",
        "payload": {"capability": "summarize-pdf", "input_data": {"url": "doc.pdf"}, "agent_id": "demo-2"},
    },
    {
        "title": "5. Quarantine Path",
        "desc": "Synthesis succeeds but sandbox healthcheck fails -> quarantined",
        "payload": {"capability": "flaky-service", "input_data": {}, "agent_id": "demo-1"},
    },
    {
        "title": "6. Partial Success",
        "desc": "Draft generation fails -- returns partial results with gap report",
        "payload": {"capability": "fail-draft-impossible", "input_data": {}, "agent_id": "demo-1"},
    },
]


async def main():
    cache = DemoSkillCache()

    set_adapters(
        capability_detector=DemoCapabilityDetector(),
        skill_registry=DemoSkillRegistry(),
        docs_crawler=DemoDocsCrawler(),
        grounding_provider=DemoGroundingProvider(),
        trust_verifier=DemoCivicVerifier(),
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
            data = resp.json()
            print(json.dumps(data, indent=2))

        # Final metrics
        print(f"\n{'='*60}")
        print("  METRICS")
        print(f"{'='*60}")
        metrics = (await client.get("/metrics")).json()
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
