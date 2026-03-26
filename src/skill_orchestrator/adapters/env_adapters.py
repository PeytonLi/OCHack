"""Environment-based adapter stubs for production use.

Each adapter reads its API key from an environment variable.
If the key is missing, the adapter degrades gracefully (returns
safe defaults that let the pipeline continue without crashing).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EnvCapabilityDetector:
    """Friendli-backed capability detector. Requires FRIENDLI_API_KEY."""

    def __init__(self) -> None:
        self.api_key = os.environ.get("FRIENDLI_API_KEY")
        if not self.api_key:
            logger.warning("FRIENDLI_API_KEY not set -- capability detection will assume all capabilities are unknown")

    async def detect_gap(self, capability: str) -> bool:
        if not self.api_key:
            return True  # assume unknown when no API key
        # TODO: call Friendli API
        return True

    async def generate_draft(self, capability: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.api_key:
            return None
        # TODO: call Friendli API
        return None


class EnvSkillRegistry:
    """ClawHub-backed skill registry. Requires APIFY_API_TOKEN."""

    def __init__(self) -> None:
        self.api_token = os.environ.get("APIFY_API_TOKEN")
        if not self.api_token:
            logger.warning("APIFY_API_TOKEN not set -- ClawHub search disabled")

    async def search(self, capability: str) -> Optional[Dict[str, Any]]:
        if not self.api_token:
            return None
        # TODO: call ClawHub/Apify API
        return None


class EnvDocsCrawler:
    """Apify-backed docs crawler. Requires APIFY_API_TOKEN."""

    def __init__(self) -> None:
        self.api_token = os.environ.get("APIFY_API_TOKEN")
        if not self.api_token:
            logger.warning("APIFY_API_TOKEN not set -- docs crawling will return empty results")

    async def crawl_docs(self, capability: str) -> List[Dict[str, Any]]:
        if not self.api_token:
            return []
        # TODO: call Apify API
        return []


class EnvGroundingProvider:
    """Contextual AI-backed grounding. Requires CONTEXTUAL_API_KEY."""

    def __init__(self) -> None:
        self.api_key = os.environ.get("CONTEXTUAL_API_KEY")
        if not self.api_key:
            logger.warning("CONTEXTUAL_API_KEY not set -- grounding will return empty schema with low confidence")

    async def extract_schema(self, raw_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.api_key:
            return {"schema": "unavailable", "fields": []}
        # TODO: call Contextual AI API
        return {"schema": "unavailable", "fields": []}

    async def confidence_score(self, skill: Dict[str, Any]) -> float:
        if not self.api_key:
            return 0.0
        # TODO: call Contextual AI API
        return 0.0


class EnvTrustVerifier:
    """Civic-backed trust verification. Requires CIVIC_API_KEY."""

    def __init__(self) -> None:
        self.api_key = os.environ.get("CIVIC_API_KEY")
        if not self.api_key:
            logger.warning("CIVIC_API_KEY not set -- trust verification will allow all skills")

    async def verify(self, skill: Dict[str, Any]) -> bool:
        if not self.api_key:
            return True  # permissive when unconfigured
        # TODO: call Civic API
        return True


class EnvSkillCache:
    """Redis-backed cache. Requires REDIS_URL."""

    def __init__(self) -> None:
        self.redis_url = os.environ.get("REDIS_URL")
        self._memory: Dict[str, Dict[str, Any]] = {}
        if not self.redis_url:
            logger.warning("REDIS_URL not set -- using in-memory cache (not shared across agents)")

    async def get(self, capability: str) -> Optional[Dict[str, Any]]:
        if not self.redis_url:
            return self._memory.get(capability)
        # TODO: call Redis
        return self._memory.get(capability)

    async def set(self, capability: str, resolution: Dict[str, Any], ttl: int = 300) -> None:
        if not self.redis_url:
            self._memory[capability] = resolution
            return
        # TODO: call Redis
        self._memory[capability] = resolution


class EnvRuntimeSandbox:
    """Local sandbox for skill execution."""

    def validate_configuration(self) -> None:
        return None

    async def install(self, skill: Dict[str, Any]) -> bool:
        return True

    async def healthcheck(self, skill: Dict[str, Any]) -> bool:
        return True

    async def execute(self, skill: Dict[str, Any], input_data: Dict[str, Any]) -> Any:
        return {"output": f"Executed {skill.get('name', 'unknown')}", "skill": skill}

    async def rollback(self, skill: Dict[str, Any]) -> None:
        pass
