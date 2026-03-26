"""CapabilityRouter: routes incoming requests through detection and discovery."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from skill_orchestrator.models import (
    CapabilityGap,
    ResolutionStrategy,
    SkillRequest,
    SkillResponse,
)
from skill_orchestrator.telemetry import telemetry

logger = logging.getLogger(__name__)

TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)
RETRY_BACKOFF_SECONDS = 0.5
PERMISSIVE_LICENSES = {
    "mit",
    "apache-2.0",
    "bsd-2-clause",
    "bsd-3-clause",
    "isc",
    "unlicense",
    "cc0-1.0",
}


async def _retry_once(fn: Callable, *args, **kwargs):
    """Call fn once; on transient error, wait and retry exactly once."""
    try:
        return await fn(*args, **kwargs)
    except TRANSIENT_ERRORS:
        logger.warning("transient error in %s, retrying after backoff", fn)
        await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        return await fn(*args, **kwargs)


class CapabilityRouter:
    def __init__(
        self,
        capability_detector,
        skill_registry,
        docs_crawler,
        grounding_provider,
        trust_verifier,
        skill_cache,
        runtime_sandbox=None,
        *,
        skill_cache_ttl_seconds: int = 300,
    ):
        self.detector = capability_detector
        self.registry = skill_registry
        self.docs_crawler = docs_crawler
        self.grounding = grounding_provider
        self.trust = trust_verifier
        self.cache = skill_cache
        self.sandbox = runtime_sandbox
        self.skill_cache_ttl_seconds = skill_cache_ttl_seconds

    async def resolve_and_run(self, request: SkillRequest) -> SkillResponse:
        capability = request.capability
        logger.info(
            "resolve_and_run: capability=%s agent=%s",
            capability,
            request.agent_id,
        )

        is_unknown = await self.detector.detect_gap(capability)
        if not is_unknown:
            return SkillResponse(
                success=True,
                capability=capability,
                resolution_strategy=ResolutionStrategy.NATIVE_CAPABILITY,
                result={
                    "status": ResolutionStrategy.NATIVE_CAPABILITY.value,
                    "capability": capability,
                },
            )

        cached = await self._cache_get(capability)
        if cached is not None:
            telemetry.record_cache_hit()
            return SkillResponse(
                success=True,
                capability=capability,
                result=cached.get("result"),
                resolution_strategy=ResolutionStrategy.LOCAL_CACHE,
            )

        try:
            registry_hit = await _retry_once(self.registry.search, capability)
        except TRANSIENT_ERRORS as exc:
            logger.warning(
                "registry search unavailable after retry for %s; continuing to synthesis: %s",
                capability,
                exc,
            )
            registry_hit = None

        if registry_hit is not None:
            result, error, healthy = await self._sandbox_execute(
                registry_hit,
                request.input_data,
            )
            if not healthy or error:
                return SkillResponse(
                    success=False,
                    capability=capability,
                    error=error or "Runtime execution failed",
                )
            await self._cache_set(capability, {"result": result})
            return SkillResponse(
                success=True,
                capability=capability,
                result=result,
                resolution_strategy=ResolutionStrategy.CLAWHUB_RETRIEVAL,
            )

        return await self._synthesize(capability, request.input_data)

    @staticmethod
    def _check_licenses(skill: Dict[str, Any]) -> list[str]:
        deps = skill.get("dependencies", [])
        blocked = []
        for dep in deps:
            lic = dep.get("license", "")
            if lic.lower() not in PERMISSIVE_LICENSES:
                blocked.append(f"{dep.get('name', '?')}:{lic}")
        return blocked

    async def _sandbox_execute(
        self, skill: Dict[str, Any], input_data: Dict[str, Any]
    ) -> tuple[Any, Optional[str], bool]:
        if self.sandbox is None:
            return None, "Runtime sandbox is not configured", False

        try:
            await self.sandbox.install(skill)
            if not await self.sandbox.healthcheck(skill):
                logger.warning("sandbox healthcheck failed, rolling back")
                await self.sandbox.rollback(skill)
                return None, "Sandbox healthcheck failed", False
            result = await self.sandbox.execute(skill, input_data)
            return result, None, True
        except Exception as exc:
            logger.warning("sandbox execution failed: %s", exc)
            try:
                await self.sandbox.rollback(skill)
            except Exception:
                logger.warning("sandbox rollback also failed", exc_info=True)
            return None, str(exc), False

    async def _synthesize(
        self, capability: str, input_data: Dict[str, Any]
    ) -> SkillResponse:
        logger.info("entering synthesis pipeline: %s", capability)
        raw_docs = await self._crawl_docs(capability)
        draft = await self.detector.generate_draft(capability, {"docs": raw_docs})

        if draft is None or not isinstance(draft, dict):
            logger.warning("synthesis failed to generate draft: %s", capability)
            return SkillResponse(
                success=False,
                capability=capability,
                result={"docs": raw_docs},
                capability_gaps=[
                    CapabilityGap(
                        capability=capability,
                        reason="Draft generation failed",
                        attempted_strategies=[
                            "local_cache",
                            "clawhub_retrieval",
                            "synthesis",
                        ],
                    )
                ],
            )

        draft_error = self._validate_generated_skill(draft, capability)
        if draft_error:
            return SkillResponse(
                success=False,
                capability=capability,
                error=draft_error,
                result={"docs": raw_docs},
            )

        blocked = self._check_licenses(draft)
        if blocked:
            return SkillResponse(
                success=False,
                capability=capability,
                error=f"Non-permissive license(s) blocked: {', '.join(blocked)}",
            )

        result, error, healthy = await self._sandbox_execute(draft, input_data)
        if not healthy or error:
            return SkillResponse(
                success=False,
                capability=capability,
                error=error or "Runtime execution failed",
            )

        await self._cache_set(capability, {"result": result})
        return SkillResponse(
            success=True,
            capability=capability,
            result=result,
            resolution_strategy=ResolutionStrategy.SYNTHESIS,
        )

    async def _crawl_docs(self, capability: str) -> list[dict[str, Any]]:
        try:
            docs = await self.docs_crawler.crawl_docs(capability)
        except Exception as exc:
            logger.warning(
                "docs crawl failed for %s; continuing with empty docs: %s",
                capability,
                exc,
            )
            return []
        return docs if isinstance(docs, list) else []

    async def _cache_get(self, capability: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.cache.get(capability)
        except Exception as exc:
            logger.warning("cache read failed for %s: %s", capability, exc)
            return None

    async def _cache_set(self, capability: str, value: Dict[str, Any]) -> None:
        try:
            await self.cache.set(
                capability,
                value,
                ttl=self.skill_cache_ttl_seconds,
            )
        except Exception as exc:
            logger.warning("cache write failed for %s: %s", capability, exc)

    @staticmethod
    def _validate_generated_skill(
        skill: Dict[str, Any], capability: str
    ) -> Optional[str]:
        name = skill.get("name")
        description = skill.get("description")
        skill_md = skill.get("skill_md")
        files = skill.get("files")

        if not isinstance(name, str) or not name.strip():
            return f"Generated skill for {capability} is missing a valid name"
        if not isinstance(description, str) or not description.strip():
            return (
                f"Generated skill for {capability} is missing a valid description"
            )
        if isinstance(skill_md, str) and skill_md.strip():
            return None
        if isinstance(files, dict):
            embedded_skill_md = files.get("SKILL.md")
            if isinstance(embedded_skill_md, str) and embedded_skill_md.strip():
                return None
        return (
            f"Generated skill for {capability} must include runnable SKILL.md content"
        )
