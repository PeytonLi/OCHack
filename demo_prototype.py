"""Prototype demo using the production bootstrap and local .env settings.

Run with: .venv/bin/python demo_prototype.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from httpx import ASGITransport, AsyncClient

from skill_orchestrator.app import create_app
from skill_orchestrator.settings import load_settings


SCENARIOS = [
    {
        "title": "1. Native Capability Check",
        "desc": "Friendli can return native_capability when no external resolution is needed",
        "payload": {
            "capability": "native-capability",
            "input_data": {},
            "agent_id": "prototype-demo",
        },
    },
    {
        "title": "2. Retrieval Or Synthesis",
        "desc": "Unknown capabilities first try ClawHub, then synthesize a runnable draft",
        "payload": {
            "capability": "summarize-pdf",
            "input_data": {"url": "doc.pdf"},
            "agent_id": "prototype-demo",
        },
    },
    {
        "title": "3. Cache Reuse",
        "desc": "Second request for the same capability should reuse the cached executed result",
        "payload": {
            "capability": "summarize-pdf",
            "input_data": {"url": "doc.pdf"},
            "agent_id": "prototype-demo-2",
        },
    },
]


def _settings_summary(settings) -> dict:
    return {
        "friendli_base_url": settings.friendli_base_url,
        "friendli_model": settings.friendli_model,
        "clawhub_base_url": settings.clawhub_base_url,
        "clawhub_bin": settings.clawhub_bin,
        "enable_apify": settings.enable_apify,
        "enable_redis": settings.enable_redis,
        "sandbox_root": settings.sandbox_root,
    }


async def main() -> None:
    settings = load_settings()
    app = create_app(settings)
    router = app.state.router

    print("=" * 60)
    print("  PROTOTYPE CONFIG")
    print("=" * 60)
    print(json.dumps(_settings_summary(settings), indent=2))
    print("adapter_classes")
    print(
        json.dumps(
            {
                "capability_detector": type(router.detector).__name__,
                "docs_crawler": type(router.docs_crawler).__name__,
                "skill_registry": type(router.registry).__name__,
                "skill_cache": type(router.cache).__name__,
                "runtime_sandbox": type(router.sandbox).__name__,
            },
            indent=2,
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for scenario in SCENARIOS:
            print(f"\n{'=' * 60}")
            print(f"  {scenario['title']}")
            print(f"  {scenario['desc']}")
            print(f"{'=' * 60}")

            response = await client.post(
                "/resolve-skill-and-run", json=scenario["payload"]
            )
            print(json.dumps(response.json(), indent=2))

        print(f"\n{'=' * 60}")
        print("  METRICS")
        print(f"{'=' * 60}")
        metrics = (await client.get("/metrics")).json()
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
