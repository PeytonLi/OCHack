from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx

from skill_orchestrator.adapters.production import (
    ApifyDocsCrawler,
    CivicTrustVerifier,
    ContextualGroundingProvider,
    FriendliCapabilityDetector,
    NullSkillRegistry,
    RedisSkillCache,
)
from skill_orchestrator.router import CapabilityRouter
from skill_orchestrator.settings import Settings


@dataclass
class ProductionResources:
    capability_detector: FriendliCapabilityDetector
    skill_registry: NullSkillRegistry
    docs_crawler: ApifyDocsCrawler
    grounding_provider: ContextualGroundingProvider
    trust_verifier: CivicTrustVerifier
    skill_cache: RedisSkillCache
    runtime_sandbox: Any = None
    closeables: Tuple[Any, ...] = ()


def build_production_router(
    settings: Settings,
    *,
    transports: Optional[Dict[str, httpx.AsyncBaseTransport]] = None,
    redis_client=None,
) -> tuple[CapabilityRouter, Tuple[Any, ...]]:
    resources = build_production_resources(
        settings, transports=transports, redis_client=redis_client
    )
    router = CapabilityRouter(
        capability_detector=resources.capability_detector,
        skill_registry=resources.skill_registry,
        docs_crawler=resources.docs_crawler,
        grounding_provider=resources.grounding_provider,
        trust_verifier=resources.trust_verifier,
        skill_cache=resources.skill_cache,
        runtime_sandbox=resources.runtime_sandbox,
    )
    return router, resources.closeables


def build_production_resources(
    settings: Settings,
    *,
    transports: Optional[Dict[str, httpx.AsyncBaseTransport]] = None,
    redis_client=None,
) -> ProductionResources:
    transports = transports or {}

    friendli_client = _build_http_client(
        settings.friendli_base_url,
        {"Authorization": f"Bearer {settings.friendli_api_key}"},
        settings.http_timeout_seconds,
        transports.get("friendli"),
    )
    apify_client = _build_http_client(
        settings.apify_base_url,
        {"Authorization": f"Bearer {settings.apify_api_token}"},
        settings.http_timeout_seconds,
        transports.get("apify"),
    )
    contextual_client = _build_http_client(
        settings.contextual_base_url,
        {"Authorization": f"Bearer {settings.contextual_api_key}"},
        settings.http_timeout_seconds,
        transports.get("contextual"),
    )
    civic_client = _build_http_client(
        settings.civic_base_url,
        {"Authorization": f"Bearer {settings.civic_api_key}"},
        settings.http_timeout_seconds,
        transports.get("civic"),
    )

    if redis_client is None:
        from redis.asyncio import from_url

        redis_client = from_url(settings.redis_url, decode_responses=True)

    resources = ProductionResources(
        capability_detector=FriendliCapabilityDetector(
            client=friendli_client,
            model=settings.friendli_model,
        ),
        skill_registry=NullSkillRegistry(),
        docs_crawler=ApifyDocsCrawler(
            client=apify_client,
            actor_id=settings.apify_docs_actor_id,
            wait_for_finish_seconds=settings.apify_wait_for_finish_seconds,
        ),
        grounding_provider=ContextualGroundingProvider(
            client=contextual_client,
            model=settings.contextual_model,
        ),
        trust_verifier=CivicTrustVerifier(
            client=civic_client,
            verify_path=settings.civic_verify_path,
        ),
        skill_cache=RedisSkillCache(redis_client),
        closeables=(
            friendli_client,
            apify_client,
            contextual_client,
            civic_client,
            redis_client,
        ),
    )
    return resources


def _build_http_client(
    base_url: str,
    headers: Dict[str, str],
    timeout_seconds: float,
    transport: Optional[httpx.AsyncBaseTransport],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=timeout_seconds,
        transport=transport,
    )
