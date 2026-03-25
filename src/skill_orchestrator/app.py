from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from skill_orchestrator.models import SkillRequest, SkillResponse
from skill_orchestrator.router import CapabilityRouter
from skill_orchestrator.telemetry import telemetry

logger = logging.getLogger(__name__)

_router: Optional[CapabilityRouter] = None


def _auto_configure() -> None:
    """Auto-configure from env-based adapters if set_adapters() hasn't been called."""
    global _router
    if _router is not None:
        return
    from skill_orchestrator.adapters.env_adapters import (
        EnvCapabilityDetector,
        EnvDocsCrawler,
        EnvGroundingProvider,
        EnvRuntimeSandbox,
        EnvSkillCache,
        EnvSkillRegistry,
        EnvTrustVerifier,
    )

    logger.info("No adapters configured — auto-configuring from environment variables")
    _router = CapabilityRouter(
        capability_detector=EnvCapabilityDetector(),
        skill_registry=EnvSkillRegistry(),
        docs_crawler=EnvDocsCrawler(),
        grounding_provider=EnvGroundingProvider(),
        trust_verifier=EnvTrustVerifier(),
        skill_cache=EnvSkillCache(),
        runtime_sandbox=EnvRuntimeSandbox(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _auto_configure()
    yield


app = FastAPI(title="Skill Orchestrator", lifespan=lifespan)


def set_adapters(
    capability_detector,
    skill_registry,
    docs_crawler,
    grounding_provider,
    trust_verifier,
    skill_cache,
    runtime_sandbox=None,
) -> None:
    """Inject adapter implementations (production or test fakes)."""
    global _router
    _router = CapabilityRouter(
        capability_detector=capability_detector,
        skill_registry=skill_registry,
        docs_crawler=docs_crawler,
        grounding_provider=grounding_provider,
        trust_verifier=trust_verifier,
        skill_cache=skill_cache,
        runtime_sandbox=runtime_sandbox,
    )
    # Reset telemetry on reconfiguration (tests get fresh state)
    telemetry.total_requests = 0
    telemetry.resolutions = 0
    telemetry.cache_hits = 0
    telemetry.blocks = 0
    telemetry.quarantines = 0
    telemetry.total_resolution_time = 0.0


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/resolve-skill-and-run", response_model=SkillResponse)
async def resolve_skill_and_run(request: SkillRequest) -> SkillResponse:
    if _router is None:
        return SkillResponse(
            success=False,
            capability=request.capability,
            error="Service not configured",
        )
    telemetry.record_request()
    start = time.monotonic()
    response = await _router.resolve_and_run(request)
    duration = time.monotonic() - start

    # Track outcomes
    if response.success:
        telemetry.record_resolution(duration)
    if response.error and "trust" in response.error.lower():
        telemetry.record_block()
    if response.publish_state and response.publish_state.value == "quarantined":
        telemetry.record_quarantine()
    if response.resolution_strategy and response.resolution_strategy.value == "local_cache":
        # Cache hit if the capability was unknown (not the "known capability" fast path)
        # We detect this by checking if the request triggered discovery
        # Simple heuristic: if we got local_cache and telemetry shows > 1 request, likely a hit
        pass  # Handled in router

    return response


@app.get("/metrics")
async def get_metrics() -> dict:
    return telemetry.snapshot()
