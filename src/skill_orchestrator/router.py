"""CapabilityRouter: routes incoming requests through detection and discovery."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict

from skill_orchestrator.telemetry import telemetry
from skill_orchestrator.models import (
    CapabilityGap,
    PublishState,
    ResolutionStrategy,
    SkillRequest,
    SkillResponse,
)

logger = logging.getLogger(__name__)

CIVIC_BLOCK_ERROR = "Blocked by trust verification (Civic)"
TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)
RETRY_BACKOFF_SECONDS = 0.5

CONFIDENCE_THRESHOLD_NORMAL = 0.7
CONFIDENCE_THRESHOLD_HIGH_RISK = 0.9
HIGH_RISK_KEYWORDS = {"shell", "network", "exec", "sudo", "admin", "delete", "drop", "filesystem"}
PERMISSIVE_LICENSES = {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "unlicense", "cc0-1.0"}


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
    ):
        self.detector = capability_detector
        self.registry = skill_registry
        self.docs_crawler = docs_crawler
        self.grounding = grounding_provider
        self.trust = trust_verifier
        self.cache = skill_cache
        self.sandbox = runtime_sandbox

    async def resolve_and_run(self, request: SkillRequest) -> SkillResponse:
        capability = request.capability
        logger.info("resolve_and_run: capability=%s agent=%s", capability, request.agent_id)

        # Step 1: Check if this is an unknown capability
        is_unknown = await self.detector.detect_gap(capability)

        if not is_unknown:
            return SkillResponse(
                success=True,
                capability=capability,
                resolution_strategy=ResolutionStrategy.LOCAL_CACHE,
            )

        # Step 2: Discovery flow - retrieval first
        logger.info("capability unknown, entering discovery: %s", capability)

        # Try local cache (Redis)
        cached = await self.cache.get(capability)
        if cached is not None:
            telemetry.record_cache_hit()
            return SkillResponse(
                success=True,
                capability=capability,
                result=cached.get("result"),
                resolution_strategy=ResolutionStrategy.LOCAL_CACHE,
            )

        # Try ClawHub registry (with retry for transient failures)
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
            try:
                trusted = await _retry_once(self.trust.verify, registry_hit)
            except TRANSIENT_ERRORS as exc:
                return SkillResponse(
                    success=False, capability=capability,
                    error=f"Transient error during trust verification after retry: {exc}",
                )
            if not trusted:
                logger.warning("civic hard-block: capability=%s", capability)
                return SkillResponse(
                    success=False,
                    capability=capability,
                    error=CIVIC_BLOCK_ERROR,
                )
            # Sandbox execute if available
            result, error, _healthy = await self._sandbox_execute(registry_hit, request.input_data)
            if error:
                return SkillResponse(success=False, capability=capability, error=error)
            response = SkillResponse(
                success=True,
                capability=capability,
                result=result,
                resolution_strategy=ResolutionStrategy.CLAWHUB_RETRIEVAL,
            )
            await self.cache.set(capability, {"result": result})
            return response

        # Step 3: Synthesis pipeline
        return await self._synthesize(capability, request.input_data)

    @staticmethod
    def _confidence_threshold(capability: str) -> float:
        """Return the confidence threshold — stricter for high-risk capabilities."""
        cap_lower = capability.lower()
        for keyword in HIGH_RISK_KEYWORDS:
            if keyword in cap_lower:
                return CONFIDENCE_THRESHOLD_HIGH_RISK
        return CONFIDENCE_THRESHOLD_NORMAL

    @staticmethod
    def _check_licenses(skill: Dict[str, Any]) -> list:
        """Return list of non-permissive licenses found in skill dependencies."""
        deps = skill.get("dependencies", [])
        blocked = []
        for dep in deps:
            lic = dep.get("license", "")
            if lic.lower() not in PERMISSIVE_LICENSES:
                blocked.append(f"{dep.get('name', '?')}:{lic}")
        return blocked

    async def _sandbox_execute(
        self, skill: Dict[str, Any], input_data: Dict[str, Any]
    ) -> tuple:
        """Install, healthcheck, and execute a skill in the sandbox.

        Returns (result, error, healthcheck_passed).
        If no sandbox is configured, returns (skill, None, True) passthrough.
        """
        if self.sandbox is None:
            return skill, None, True

        await self.sandbox.install(skill)

        if not await self.sandbox.healthcheck(skill):
            logger.warning("sandbox healthcheck failed, rolling back")
            await self.sandbox.rollback(skill)
            return None, "Sandbox healthcheck failed", False

        result = await self.sandbox.execute(skill, input_data)
        return result, None, True

    async def _synthesize(self, capability: str, input_data: Dict[str, Any]) -> SkillResponse:
        """Full synthesis chain: Apify docs → Contextual grounding → Friendli draft → Civic verify → sandbox → publish."""
        logger.info("entering synthesis pipeline: %s", capability)

        # 3a. Crawl docs (Apify)
        raw_docs = await self.docs_crawler.crawl_docs(capability)

        # 3b. Extract grounded schema (Contextual AI)
        schema = await self.grounding.extract_schema(raw_docs)

        # 3c. Generate draft skill (Friendli)
        context = {"schema": schema, "docs": raw_docs}
        draft = await self.detector.generate_draft(capability, context)
        if draft is None:
            logger.warning("synthesis failed to generate draft: %s", capability)
            return SkillResponse(
                success=False,
                capability=capability,
                result={"docs": raw_docs, "schema": schema},
                capability_gaps=[
                    CapabilityGap(
                        capability=capability,
                        reason="Draft generation failed",
                        attempted_strategies=["local_cache", "clawhub_retrieval", "synthesis"],
                    )
                ],
            )

        # 3d. Score confidence (Contextual AI) and enforce thresholds
        confidence = await self.grounding.confidence_score(draft)
        threshold = self._confidence_threshold(capability)
        logger.info("draft confidence=%.2f threshold=%.2f for %s", confidence, threshold, capability)

        if confidence < threshold:
            logger.warning("confidence below threshold: %.2f < %.2f for %s", confidence, threshold, capability)
            return SkillResponse(
                success=False,
                capability=capability,
                error=f"Confidence {confidence:.2f} below threshold {threshold:.2f} for capability",
            )

        # 3d-ii. License allowlist check
        blocked = self._check_licenses(draft)
        if blocked:
            logger.warning("non-permissive licenses found: %s", blocked)
            return SkillResponse(
                success=False,
                capability=capability,
                error=f"Non-permissive license(s) blocked: {', '.join(blocked)}",
            )

        # 3e. Civic trust verification — hard block (with retry)
        try:
            trusted = await _retry_once(self.trust.verify, draft)
        except TRANSIENT_ERRORS as exc:
            return SkillResponse(
                success=False, capability=capability,
                error=f"Transient error during trust verification after retry: {exc}",
            )
        if not trusted:
            logger.warning("civic hard-block on synthesized skill: %s", capability)
            return SkillResponse(
                success=False,
                capability=capability,
                error=CIVIC_BLOCK_ERROR,
            )

        # 3f. Sandbox execute if available
        result, _error, healthy = await self._sandbox_execute(draft, input_data)

        if healthy:
            # 3g. Publish as active and cache for reuse
            logger.info("publishing skill as active: %s", capability)
            await self.cache.set(capability, {"result": result})
            return SkillResponse(
                success=True,
                capability=capability,
                result=result,
                resolution_strategy=ResolutionStrategy.SYNTHESIS,
                publish_state=PublishState.ACTIVE,
            )
        else:
            # Policy passed but smoke test failed → quarantine
            logger.warning("smoke test failed, publishing as quarantined: %s", capability)
            return SkillResponse(
                success=True,
                capability=capability,
                result=draft,
                resolution_strategy=ResolutionStrategy.SYNTHESIS,
                publish_state=PublishState.QUARANTINED,
            )
