from __future__ import annotations

from contextlib import asynccontextmanager
import time
from typing import Iterable, Optional

from fastapi import FastAPI, Request

from skill_orchestrator.exceptions import ConfigurationError
from skill_orchestrator.factory import build_production_router
from skill_orchestrator.models import SkillRequest, SkillResponse
from skill_orchestrator.router import CapabilityRouter
from skill_orchestrator.settings import Settings, has_required_settings, load_settings
from skill_orchestrator.telemetry import telemetry


def set_adapters(
    capability_detector,
    skill_registry,
    docs_crawler,
    grounding_provider,
    trust_verifier,
    skill_cache,
    runtime_sandbox=None,
    target_app: Optional[FastAPI] = None,
) -> None:
    """Inject adapter implementations (production or test fakes)."""
    router = CapabilityRouter(
        capability_detector=capability_detector,
        skill_registry=skill_registry,
        docs_crawler=docs_crawler,
        grounding_provider=grounding_provider,
        trust_verifier=trust_verifier,
        skill_cache=skill_cache,
        runtime_sandbox=runtime_sandbox,
    )
    configured_app = target_app or app
    configured_app.state.router = router
    configured_app.state.closeables = []
    _reset_telemetry()


def create_app(
    settings: Optional[Settings] = None,
    *,
    transports=None,
    redis_client=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        yield
        await _close_all(requested_closeables=app_instance.state.closeables)

    configured_app = FastAPI(title="Skill Orchestrator", lifespan=lifespan)
    configured_app.state.router = None
    configured_app.state.closeables = []

    @configured_app.post("/resolve-skill-and-run", response_model=SkillResponse)
    async def resolve_skill_and_run(
        payload: SkillRequest, request: Request
    ) -> SkillResponse:
        router: Optional[CapabilityRouter] = request.app.state.router
        if router is None:
            return SkillResponse(
                success=False,
                capability=payload.capability,
                error="Service not configured",
            )
        telemetry.record_request()
        start = time.monotonic()
        response = await router.resolve_and_run(payload)
        duration = time.monotonic() - start

        # Track outcomes
        if response.success:
            telemetry.record_resolution(duration)
        if response.error and "trust" in response.error.lower():
            telemetry.record_block()
        if (
            response.publish_state
            and response.publish_state.value == "quarantined"
        ):
            telemetry.record_quarantine()
        if (
            response.resolution_strategy
            and response.resolution_strategy.value == "local_cache"
        ):
            # Cache hit if the capability was unknown (not the known-capability fast path).
            pass  # Handled in router

        return response

    @configured_app.get("/metrics")
    async def get_metrics() -> dict:
        return telemetry.snapshot()

    if settings is not None:
        router, closeables = build_production_router(
            settings,
            transports=transports,
            redis_client=redis_client,
        )
        configured_app.state.router = router
        configured_app.state.closeables = list(closeables)
        _reset_telemetry()

    return configured_app


def _reset_telemetry() -> None:
    telemetry.total_requests = 0
    telemetry.resolutions = 0
    telemetry.cache_hits = 0
    telemetry.blocks = 0
    telemetry.quarantines = 0
    telemetry.total_resolution_time = 0.0


async def _close_all(requested_closeables: Iterable[object]) -> None:
    for resource in requested_closeables:
        closer = getattr(resource, "aclose", None) or getattr(resource, "close", None)
        if callable(closer):
            maybe_result = closer()
            if hasattr(maybe_result, "__await__"):
                await maybe_result


def _build_default_app() -> FastAPI:
    if has_required_settings():
        try:
            return create_app(load_settings())
        except ConfigurationError:
            pass
    return create_app()


app = _build_default_app()
