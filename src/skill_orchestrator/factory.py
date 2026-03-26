from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx

from skill_orchestrator.adapters.production import (
    ApifyDocsCrawler,
    CivicTrustVerifier,
    ClawHubDocsCrawler,
    ClawHubSkillRegistry,
    ContextualGroundingProvider,
    FallbackDocsCrawler,
    FriendliCapabilityDetector,
    InMemorySkillCache,
    LocalDocsCrawler,
    LocalGroundingProvider,
    PermissiveTrustVerifier,
    PrototypeCapabilityDetector,
    RedisPayloadCache,
    RedisSkillCache,
)
from skill_orchestrator.router import CapabilityRouter
from skill_orchestrator.settings import Settings


@dataclass
class ProductionResources:
    capability_detector: Any
    skill_registry: Any
    docs_crawler: Any
    grounding_provider: Any
    trust_verifier: Any
    skill_cache: Any
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
    clawhub_client = _build_http_client(
        settings.clawhub_base_url,
        {},
        settings.http_timeout_seconds,
        transports.get("clawhub"),
    )

    apify_client = None
    if settings.enable_apify:
        apify_client = _build_http_client(
            settings.apify_base_url,
            {"Authorization": f"Bearer {settings.apify_api_token}"},
            settings.http_timeout_seconds,
            transports.get("apify"),
        )

    contextual_client = None
    if settings.enable_contextual:
        contextual_client = _build_http_client(
            settings.contextual_base_url,
            {"Authorization": f"Bearer {settings.contextual_api_key}"},
            settings.http_timeout_seconds,
            transports.get("contextual"),
        )

    civic_client = None
    if settings.enable_civic:
        civic_client = _build_http_client(
            settings.civic_base_url,
            {"Authorization": f"Bearer {settings.civic_api_key}"},
            settings.http_timeout_seconds,
            transports.get("civic"),
        )

    if redis_client is None and settings.enable_redis:
        from redis.asyncio import from_url

        redis_client = from_url(settings.redis_url, decode_responses=True)

    clawhub_payload_cache = (
        RedisPayloadCache(redis_client, namespace="clawhub-download")
        if settings.enable_redis and redis_client is not None
        else None
    )

    clawhub_docs = ClawHubDocsCrawler(
        client=clawhub_client,
        search_limit=max(settings.clawhub_search_limit, settings.clawhub_docs_limit),
        docs_limit=settings.clawhub_docs_limit,
        min_search_score=settings.clawhub_min_search_score,
        non_suspicious_only=settings.clawhub_non_suspicious_only,
        file_path=settings.clawhub_skill_file_path,
        tag=settings.clawhub_tag,
        payload_cache=clawhub_payload_cache,
        cache_ttl=settings.clawhub_cache_ttl_seconds,
    )
    if settings.enable_apify and apify_client is not None:
        docs_crawler = FallbackDocsCrawler(
            clawhub_docs,
            ApifyDocsCrawler(
                client=apify_client,
                actor_id=settings.apify_docs_actor_id,
                wait_for_finish_seconds=settings.apify_wait_for_finish_seconds,
                intended_usage_template=settings.apify_intended_usage_template,
                improvement_suggestions=settings.apify_improvement_suggestions,
                contact=settings.apify_contact,
                max_items=settings.apify_max_items,
                download_content=settings.apify_download_content,
            ),
            LocalDocsCrawler(),
        )
    else:
        docs_crawler = FallbackDocsCrawler(
            clawhub_docs,
            LocalDocsCrawler(),
        )
    skill_registry = ClawHubSkillRegistry(
        client=clawhub_client,
        search_limit=settings.clawhub_search_limit,
        min_search_score=settings.clawhub_min_search_score,
        non_suspicious_only=settings.clawhub_non_suspicious_only,
        file_path=settings.clawhub_skill_file_path,
        tag=settings.clawhub_tag,
        payload_cache=clawhub_payload_cache,
        cache_ttl=settings.clawhub_cache_ttl_seconds,
    )
    grounding_provider = (
        ContextualGroundingProvider(
            client=contextual_client,
            model=settings.contextual_model,
        )
        if settings.enable_contextual and contextual_client is not None
        else LocalGroundingProvider()
    )
    trust_verifier = (
        CivicTrustVerifier(
            client=civic_client,
            verify_path=settings.civic_verify_path,
        )
        if settings.enable_civic and civic_client is not None
        else PermissiveTrustVerifier()
    )
    skill_cache = (
        RedisSkillCache(redis_client)
        if settings.enable_redis and redis_client is not None
        else InMemorySkillCache()
    )

    closeables = [friendli_client, clawhub_client]
    for resource in (apify_client, contextual_client, civic_client):
        if resource is not None:
            closeables.append(resource)
    if settings.enable_redis and redis_client is not None:
        closeables.append(redis_client)

    friendli_detector = FriendliCapabilityDetector(
        client=friendli_client,
        model=settings.friendli_model,
    )
    capability_detector = (
        friendli_detector
        if any(
            (
                settings.enable_apify,
                settings.enable_contextual,
                settings.enable_civic,
                settings.enable_redis,
            )
        )
        else PrototypeCapabilityDetector(friendli_detector)
    )

    resources = ProductionResources(
        capability_detector=capability_detector,
        skill_registry=skill_registry,
        docs_crawler=docs_crawler,
        grounding_provider=grounding_provider,
        trust_verifier=trust_verifier,
        skill_cache=skill_cache,
        closeables=tuple(closeables),
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
