from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol


class CapabilityDetector(Protocol):
    """Friendli: detects capability gaps and generates draft skills."""

    async def detect_gap(self, capability: str) -> bool:
        """Returns True if the capability is unknown/missing."""
        ...

    async def generate_draft(self, capability: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Generate a draft skill implementation."""
        ...


class SkillRegistry(Protocol):
    """ClawHub: skill search and retrieval."""

    async def search(self, capability: str) -> Optional[Dict[str, Any]]:
        """Search for an existing skill by capability name."""
        ...


class DocsCrawler(Protocol):
    """Apify: documentation crawling fallback."""

    async def crawl_docs(self, capability: str) -> List[Dict[str, Any]]:
        """Crawl documentation for a given capability."""
        ...


class GroundingProvider(Protocol):
    """Contextual AI: grounded schema extraction and confidence scoring."""

    async def extract_schema(self, raw_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Extract grounded schema from raw documentation."""
        ...

    async def confidence_score(self, skill: Dict[str, Any]) -> float:
        """Score confidence in a skill's correctness."""
        ...


class TrustVerifier(Protocol):
    """Civic: trust verification and policy authority."""

    async def verify(self, skill: Dict[str, Any]) -> bool:
        """Verify a skill meets trust/safety policy. Hard block on failure."""
        ...


class RuntimeSandbox(Protocol):
    """Sandbox: install, healthcheck, execute, and rollback skills."""

    async def install(self, skill: Dict[str, Any]) -> bool:
        """Install a skill in the sandbox. Returns True on success."""
        ...

    async def healthcheck(self, skill: Dict[str, Any]) -> bool:
        """Run a healthcheck on installed skill. Returns True if healthy."""
        ...

    async def execute(self, skill: Dict[str, Any], input_data: Dict[str, Any]) -> Any:
        """Execute a skill with given input. Returns the result."""
        ...

    async def rollback(self, skill: Dict[str, Any]) -> None:
        """Rollback/uninstall a skill from the sandbox."""
        ...


class SkillCache(Protocol):
    """Redis: short-term cross-agent memory."""

    async def get(self, capability: str) -> Optional[Dict[str, Any]]:
        """Get cached skill resolution."""
        ...

    async def set(self, capability: str, resolution: Dict[str, Any], ttl: int = 300) -> None:
        """Cache a skill resolution."""
        ...
