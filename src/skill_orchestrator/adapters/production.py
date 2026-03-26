from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

import httpx

from skill_orchestrator.exceptions import (
    ProviderError,
    ProviderAuthError,
    ProviderResponseError,
    TransientProviderError,
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_CACHE_MISS = object()

logger = logging.getLogger(__name__)


class RedisSkillCache:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def get(self, capability: str) -> Optional[Dict[str, Any]]:
        try:
            raw = await self.redis.get(self._key(capability))
        except Exception as exc:  # pragma: no cover - backend-specific errors
            raise TransientProviderError(f"Redis get failed: {exc}") from exc

        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, dict):
            return raw

        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None

    async def set(
        self, capability: str, resolution: Dict[str, Any], ttl: int = 300
    ) -> None:
        payload = json.dumps(resolution)
        try:
            if hasattr(self.redis, "setex"):
                await self.redis.setex(self._key(capability), ttl, payload)
            else:
                await self.redis.set(self._key(capability), payload, ex=ttl)
        except Exception as exc:  # pragma: no cover - backend-specific errors
            raise TransientProviderError(f"Redis set failed: {exc}") from exc

    async def aclose(self) -> None:
        closer = getattr(self.redis, "aclose", None)
        if callable(closer):
            await closer()

    @staticmethod
    def _key(capability: str) -> str:
        return f"skill-resolution:{capability}"


class RedisPayloadCache:
    """Best-effort Redis cache for provider payloads."""

    def __init__(self, redis_client, *, namespace: str = "provider-payload"):
        self.redis = redis_client
        self.namespace = namespace

    async def get(self, key: str) -> Any:
        try:
            raw = await self.redis.get(self._key(key))
        except Exception as exc:  # pragma: no cover - backend-specific errors
            logger.warning("Redis payload cache get failed for %s: %s", key, exc)
            return _CACHE_MISS

        if raw is None:
            return _CACHE_MISS
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, dict) and "value" in raw:
            return raw["value"]

        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return _CACHE_MISS
        if not isinstance(decoded, dict) or "value" not in decoded:
            return _CACHE_MISS
        return decoded["value"]

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        payload = json.dumps({"value": value})
        try:
            if hasattr(self.redis, "setex"):
                await self.redis.setex(self._key(key), ttl, payload)
            else:
                await self.redis.set(self._key(key), payload, ex=ttl)
        except Exception as exc:  # pragma: no cover - backend-specific errors
            logger.warning("Redis payload cache set failed for %s: %s", key, exc)

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"


class InMemorySkillCache:
    def __init__(self):
        self.store: Dict[str, Dict[str, Any]] = {}

    async def get(self, capability: str) -> Optional[Dict[str, Any]]:
        return self.store.get(capability)

    async def set(
        self, capability: str, resolution: Dict[str, Any], ttl: int = 300
    ) -> None:
        self.store[capability] = resolution


class NullSkillRegistry:
    async def search(self, capability: str) -> None:
        return None


class LocalDocsCrawler:
    async def crawl_docs(self, capability: str) -> List[Dict[str, Any]]:
        return [
            {
                "source": "local-fallback",
                "content": f"No external documentation crawler configured for capability: {capability}",
            }
        ]


class FallbackDocsCrawler:
    def __init__(self, *crawlers):
        self.crawlers = tuple(crawler for crawler in crawlers if crawler is not None)

    async def crawl_docs(self, capability: str) -> List[Dict[str, Any]]:
        last_error: Optional[Exception] = None
        for crawler in self.crawlers:
            try:
                docs = await crawler.crawl_docs(capability)
            except (ProviderError, ConnectionError, TimeoutError, OSError) as exc:
                last_error = exc
                continue
            if docs:
                return docs
        if last_error is not None:
            raise last_error
        return []


class LocalGroundingProvider:
    async def extract_schema(self, raw_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        fields = []
        if raw_docs:
            fields.append("context")
        return {"schema": "prototype", "fields": fields}

    async def confidence_score(self, skill: Dict[str, Any]) -> float:
        return 0.95 if isinstance(skill, dict) and skill else 0.0


class PermissiveTrustVerifier:
    async def verify(self, skill: Dict[str, Any]) -> bool:
        return True


class PrototypeCapabilityDetector:
    def __init__(self, delegate: "FriendliCapabilityDetector"):
        self.delegate = delegate

    async def detect_gap(self, capability: str) -> bool:
        # Prototype mode always treats requests as unresolved so synthesis runs.
        return True

    async def generate_draft(
        self, capability: str, context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            draft = await self.delegate.generate_draft(capability, context)
        except Exception:
            draft = None

        if isinstance(draft, dict) and draft:
            draft.setdefault("name", capability)
            draft.setdefault("version", "0.1.0")
            draft.setdefault("dependencies", [])
            return draft

        return {
            "name": capability,
            "description": f"Prototype draft generated locally for capability: {capability}",
            "code": "pass",
            "version": "0.1.0",
            "dependencies": [],
        }


class HttpJsonAdapter:
    def __init__(self, client: httpx.AsyncClient, provider_name: str):
        self.client = client
        self.provider_name = provider_name

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _request(
        self, method: str, path: str, *, allow_not_found: bool = False, **kwargs
    ) -> Optional[httpx.Response]:
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"{self.provider_name} request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"{self.provider_name} request failed: {exc}"
            ) from exc

        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise TransientProviderError(
                f"{self.provider_name} transient error {response.status_code}: "
                f"{_truncate(response.text)}"
            )
        if response.status_code in {401, 403}:
            raise ProviderAuthError(
                f"{self.provider_name} auth failed with status "
                f"{response.status_code}"
            )
        if response.status_code >= 400:
            raise ProviderResponseError(
                f"{self.provider_name} returned status {response.status_code}: "
                f"{_truncate(response.text)}"
            )

        return response

    async def _request_json(self, method: str, path: str, **kwargs) -> Any:
        response = await self._request(method, path, **kwargs)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                f"{self.provider_name} returned invalid JSON"
            ) from exc

    async def _request_text(self, method: str, path: str, **kwargs) -> Optional[str]:
        response = await self._request(method, path, **kwargs)
        if response is None:
            return None
        return response.text

    async def _post_json(self, path: str, payload: Dict[str, Any], **kwargs) -> Any:
        return await self._request_json("POST", path, json=payload, **kwargs)

    def _extract_llm_json(self, payload: Dict[str, Any]) -> Any:
        text = _extract_text(payload)
        return _parse_json_text(text)


class ClawHubSkillRegistry(HttpJsonAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        search_limit: int = 5,
        min_search_score: float = 1.2,
        non_suspicious_only: bool = True,
        file_path: str = "SKILL.md",
        tag: str = "latest",
        payload_cache: Optional[RedisPayloadCache] = None,
        cache_ttl: int = 3600,
    ):
        super().__init__(client, "ClawHub")
        self.search_limit = search_limit
        self.min_search_score = min_search_score
        self.non_suspicious_only = non_suspicious_only
        self.file_path = file_path
        self.tag = tag
        self.payload_cache = payload_cache
        self.cache_ttl = cache_ttl

    async def search(self, capability: str) -> Optional[Dict[str, Any]]:
        exact = await self._fetch_exact_match(capability)
        if exact is not None:
            return exact

        results = await self._search_results(capability, limit=self.search_limit)
        if not results:
            return None

        match = self._select_best_match(capability, results)
        if match is None:
            return None

        slug = match.get("slug")
        if not isinstance(slug, str) or not slug:
            return None
        return await self._fetch_skill(slug, search_result=match)

    async def _fetch_exact_match(self, capability: str) -> Optional[Dict[str, Any]]:
        for slug in _slug_candidates(capability):
            skill = await self._fetch_skill(
                slug,
                search_result={
                    "slug": slug,
                    "displayName": capability,
                    "summary": None,
                    "version": None,
                    "score": None,
                },
                allow_not_found=True,
            )
            if skill is not None:
                return skill
        return None

    async def _search_results(
        self, capability: str, *, limit: int
    ) -> List[Dict[str, Any]]:
        cached = await self._cache_get(
            "search",
            capability=capability,
            limit=limit,
            non_suspicious_only=self.non_suspicious_only,
        )
        if isinstance(cached, list):
            return [item for item in cached if isinstance(item, dict)]

        params: Dict[str, Any] = {"q": capability, "limit": limit}
        if self.non_suspicious_only:
            params["nonSuspiciousOnly"] = True
        payload = await self._request_json("GET", "/api/v1/search", params=params)
        if isinstance(payload, dict):
            results = payload.get("results", payload.get("items"))
        elif isinstance(payload, list):
            results = payload
        else:
            results = None
        if results is None:
            await self._cache_set(
                "search",
                [],
                capability=capability,
                limit=limit,
                non_suspicious_only=self.non_suspicious_only,
            )
            return []
        if not isinstance(results, list):
            raise ProviderResponseError("ClawHub search response was not a list")
        filtered = [item for item in results if isinstance(item, dict)]
        await self._cache_set(
            "search",
            filtered,
            capability=capability,
            limit=limit,
            non_suspicious_only=self.non_suspicious_only,
        )
        return filtered

    async def _fetch_skill(
        self,
        slug: str,
        *,
        search_result: Optional[Dict[str, Any]] = None,
        allow_not_found: bool = False,
    ) -> Optional[Dict[str, Any]]:
        detail = await self._fetch_skill_detail(
            slug,
            allow_not_found=allow_not_found,
        )
        if detail is None:
            return None
        skill_md = await self._fetch_skill_file(slug)
        return _build_clawhub_skill(
            detail,
            search_result=search_result,
            skill_md=skill_md,
        )

    async def _fetch_skill_detail(
        self, slug: str, *, allow_not_found: bool = False
    ) -> Optional[Dict[str, Any]]:
        cached = await self._cache_get(
            "detail",
            slug=slug,
            allow_not_found=allow_not_found,
        )
        if cached is None:
            return None
        if isinstance(cached, dict):
            return cached

        detail = await self._request_json(
            "GET",
            f"/api/v1/skills/{slug}",
            allow_not_found=allow_not_found,
        )
        if detail is not None and not isinstance(detail, dict):
            raise ProviderResponseError("ClawHub skill detail response was not an object")
        await self._cache_set(
            "detail",
            detail,
            slug=slug,
            allow_not_found=allow_not_found,
        )
        return detail

    async def _fetch_skill_file(self, slug: str) -> Optional[str]:
        cached = await self._cache_get(
            "file",
            slug=slug,
            path=self.file_path,
            tag=self.tag or "",
        )
        if cached is None:
            return None
        if isinstance(cached, str):
            return cached

        file_params: Dict[str, Any] = {"path": self.file_path}
        if self.tag:
            file_params["tag"] = self.tag
        skill_md = await self._request_text(
            "GET",
            f"/api/v1/skills/{slug}/file",
            params=file_params,
            allow_not_found=True,
        )
        await self._cache_set(
            "file",
            skill_md,
            slug=slug,
            path=self.file_path,
            tag=self.tag or "",
        )
        return skill_md

    async def _cache_get(self, kind: str, **parts: Any) -> Any:
        if self.payload_cache is None:
            return _CACHE_MISS
        return await self.payload_cache.get(_cache_key(kind, **parts))

    async def _cache_set(self, kind: str, value: Any, **parts: Any) -> None:
        if self.payload_cache is None:
            return
        await self.payload_cache.set(
            _cache_key(kind, **parts),
            value,
            ttl=self.cache_ttl,
        )

    def _select_best_match(
        self, capability: str, results: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        normalized_query = _normalize_skill_key(capability)
        for result in results:
            slug = _normalize_skill_key(result.get("slug"))
            display_name = _normalize_skill_key(result.get("displayName"))
            if normalized_query and normalized_query in {slug, display_name}:
                return result

        top_result = results[0]
        score = _as_float(top_result.get("score"))
        if score is None or score < self.min_search_score:
            return None
        return top_result


class ClawHubDocsCrawler(HttpJsonAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        search_limit: int = 5,
        docs_limit: int = 3,
        min_search_score: float = 1.2,
        non_suspicious_only: bool = True,
        file_path: str = "SKILL.md",
        tag: str = "latest",
        payload_cache: Optional[RedisPayloadCache] = None,
        cache_ttl: int = 3600,
    ):
        super().__init__(client, "ClawHub")
        self.search_limit = search_limit
        self.docs_limit = docs_limit
        self.min_search_score = min_search_score
        self.non_suspicious_only = non_suspicious_only
        self.file_path = file_path
        self.tag = tag
        self.payload_cache = payload_cache
        self.cache_ttl = cache_ttl

    async def crawl_docs(self, capability: str) -> List[Dict[str, Any]]:
        cached_results = await self._cache_get(
            "search",
            capability=capability,
            limit=self.search_limit,
            non_suspicious_only=self.non_suspicious_only,
        )
        if isinstance(cached_results, list):
            results = [item for item in cached_results if isinstance(item, dict)]
        else:
            params: Dict[str, Any] = {"q": capability, "limit": self.search_limit}
            if self.non_suspicious_only:
                params["nonSuspiciousOnly"] = True
            payload = await self._request_json("GET", "/api/v1/search", params=params)
            if isinstance(payload, dict):
                results = payload.get("results", payload.get("items"))
            elif isinstance(payload, list):
                results = payload
            else:
                results = None
            if isinstance(results, list):
                results = [item for item in results if isinstance(item, dict)]
            else:
                results = []
            await self._cache_set(
                "search",
                results,
                capability=capability,
                limit=self.search_limit,
                non_suspicious_only=self.non_suspicious_only,
            )
        if not isinstance(results, list):
            return []

        docs: List[Dict[str, Any]] = []
        normalized_query = _normalize_skill_key(capability)
        for result in results:
            if not isinstance(result, dict):
                continue
            slug = result.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            score = _as_float(result.get("score"))
            slug_match = _normalize_skill_key(slug) == normalized_query
            display_match = (
                _normalize_skill_key(result.get("displayName")) == normalized_query
            )
            if not (
                slug_match
                or display_match
                or (score is not None and score >= self.min_search_score)
            ):
                continue

            detail = await self._fetch_skill_detail(slug, allow_not_found=True)
            if not isinstance(detail, dict):
                continue

            skill_md = await self._fetch_skill_file(slug)

            skill = detail.get("skill", {})
            latest_version = detail.get("latestVersion", {})
            summary = None
            if isinstance(skill, dict):
                summary = skill.get("summary")

            content_parts = []
            if isinstance(summary, str) and summary:
                content_parts.append(f"Summary: {summary}")
            if isinstance(skill_md, str) and skill_md.strip():
                content_parts.append(skill_md)
            if not content_parts:
                continue

            docs.append(
                {
                    "source": "clawhub",
                    "slug": slug,
                    "display_name": _coalesce(
                        skill.get("displayName") if isinstance(skill, dict) else None,
                        result.get("displayName"),
                        slug,
                    ),
                    "summary": summary or result.get("summary"),
                    "version": (
                        latest_version.get("version")
                        if isinstance(latest_version, dict)
                        else result.get("version")
                    ),
                    "search_score": score,
                    "content": "\n\n".join(content_parts),
                }
            )
            if len(docs) >= self.docs_limit:
                break

        return docs

    async def _fetch_skill_detail(
        self, slug: str, *, allow_not_found: bool = False
    ) -> Optional[Dict[str, Any]]:
        cached = await self._cache_get(
            "detail",
            slug=slug,
            allow_not_found=allow_not_found,
        )
        if cached is None:
            return None
        if isinstance(cached, dict):
            return cached

        detail = await self._request_json(
            "GET",
            f"/api/v1/skills/{slug}",
            allow_not_found=allow_not_found,
        )
        if detail is not None and not isinstance(detail, dict):
            raise ProviderResponseError("ClawHub skill detail response was not an object")
        await self._cache_set(
            "detail",
            detail,
            slug=slug,
            allow_not_found=allow_not_found,
        )
        return detail

    async def _fetch_skill_file(self, slug: str) -> Optional[str]:
        cached = await self._cache_get(
            "file",
            slug=slug,
            path=self.file_path,
            tag=self.tag or "",
        )
        if cached is None:
            return None
        if isinstance(cached, str):
            return cached

        file_params: Dict[str, Any] = {"path": self.file_path}
        if self.tag:
            file_params["tag"] = self.tag
        skill_md = await self._request_text(
            "GET",
            f"/api/v1/skills/{slug}/file",
            params=file_params,
            allow_not_found=True,
        )
        await self._cache_set(
            "file",
            skill_md,
            slug=slug,
            path=self.file_path,
            tag=self.tag or "",
        )
        return skill_md

    async def _cache_get(self, kind: str, **parts: Any) -> Any:
        if self.payload_cache is None:
            return _CACHE_MISS
        return await self.payload_cache.get(_cache_key(kind, **parts))

    async def _cache_set(self, kind: str, value: Any, **parts: Any) -> None:
        if self.payload_cache is None:
            return
        await self.payload_cache.set(
            _cache_key(kind, **parts),
            value,
            ttl=self.cache_ttl,
        )


class ApifyDocsCrawler(HttpJsonAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient,
        actor_id: str,
        wait_for_finish_seconds: int = 60,
        intended_usage_template: str = (
            "Resolve or synthesize a skill for capability: {capability}."
        ),
        improvement_suggestions: str = (
            "Return structured skill metadata and detailed content relevant to the requested capability."
        ),
        contact: str = "",
        max_items: int = 25,
        download_content: bool = True,
    ):
        super().__init__(client, "Apify")
        self.actor_id = actor_id
        self.wait_for_finish_seconds = wait_for_finish_seconds
        self.intended_usage_template = intended_usage_template
        self.improvement_suggestions = improvement_suggestions
        self.contact = contact
        self.max_items = max_items
        self.download_content = download_content

    async def crawl_docs(self, capability: str) -> List[Dict[str, Any]]:
        run_payload = {
            "sp_intended_usage": self.intended_usage_template.format(
                capability=capability
            ),
            "sp_improvement_suggestions": self.improvement_suggestions,
            "maxItems": self.max_items,
            "downloadContent": self.download_content,
        }
        if self.contact:
            run_payload["sp_contact"] = self.contact
        items = await self._post_json(
            f"/acts/{self.actor_id}/run-sync-get-dataset-items",
            run_payload,
            params={"waitForFinish": self.wait_for_finish_seconds},
        )
        if isinstance(items, dict):
            items = items.get("data", items.get("items"))
        if not isinstance(items, list):
            raise ProviderResponseError(
                "Apify sync dataset-items response was not a list"
            )
        return items


class FriendliCapabilityDetector(HttpJsonAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient,
        model: str,
        chat_path: str = "/chat/completions",
    ):
        super().__init__(client, "Friendli")
        self.model = model
        self.chat_path = chat_path

    async def detect_gap(self, capability: str) -> bool:
        payload = await self._chat_json(
            (
                "Return JSON only. Determine whether the capability is missing from "
                'the current toolset. Respond with {"unknown": true|false}.'
            ),
            f"Capability: {capability}",
        )
        unknown = payload.get("unknown")
        if not isinstance(unknown, bool):
            raise ProviderResponseError(
                "Friendli detect response must include boolean 'unknown'"
            )
        return unknown

    async def generate_draft(
        self, capability: str, context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        payload = await self._chat_json(
            (
                "Return JSON only. Produce a draft skill definition using the "
                'provided capability and context. Respond with {"draft": <object|null>}.'
            ),
            json.dumps({"capability": capability, "context": context}),
        )
        draft = payload.get("draft", payload)
        if draft is None:
            return None
        if not isinstance(draft, dict):
            raise ProviderResponseError("Friendli draft response must be an object")
        return draft

    async def _chat_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        response = await self._post_json(
            self.chat_path,
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        parsed = self._extract_llm_json(response)
        if not isinstance(parsed, dict):
            raise ProviderResponseError("Friendli JSON response must be an object")
        return parsed


class ContextualGroundingProvider(HttpJsonAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient,
        model: str,
        generate_path: str = "/generate",
    ):
        super().__init__(client, "Contextual AI")
        self.model = model
        self.generate_path = generate_path

    async def extract_schema(self, raw_docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = await self._generate_json(
            (
                "Return JSON only. Extract a grounded schema from the provided docs. "
                "Respond with a JSON object."
            ),
            {"documents": raw_docs},
        )
        if not isinstance(payload, dict):
            raise ProviderResponseError(
                "Contextual AI schema extraction must return an object"
            )
        return payload

    async def confidence_score(self, skill: Dict[str, Any]) -> float:
        payload = await self._generate_json(
            (
                "Return JSON only. Score confidence that the skill is correct. "
                'Respond with {"confidence": 0.0-1.0}.'
            ),
            {"skill": skill},
        )
        confidence = payload.get("confidence")
        if not isinstance(confidence, (int, float)):
            raise ProviderResponseError(
                "Contextual AI confidence response must include numeric 'confidence'"
            )
        return float(confidence)

    async def _generate_json(
        self, instruction: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        response = await self._post_json(
            self.generate_path,
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": f"{instruction}\n\n{json.dumps(payload)}",
                    }
                ],
                "response_format": {"type": "json_object"},
            },
        )
        parsed = self._extract_llm_json(response)
        if not isinstance(parsed, dict):
            raise ProviderResponseError(
                "Contextual AI JSON response must be an object"
            )
        return parsed


class CivicTrustVerifier(HttpJsonAdapter):
    def __init__(
        self,
        client: httpx.AsyncClient,
        verify_path: str = "/trust/verify",
    ):
        super().__init__(client, "Civic")
        self.verify_path = verify_path

    async def verify(self, skill: Dict[str, Any]) -> bool:
        payload = await self._post_json(self.verify_path, {"skill": skill})
        for key in ("trusted", "approved", "allowed"):
            if key in payload:
                value = payload[key]
                if not isinstance(value, bool):
                    raise ProviderResponseError(
                        f"Civic response field '{key}' must be boolean"
                    )
                return value
        raise ProviderResponseError(
            "Civic verify response must include one of: trusted, approved, allowed"
        )


def _extract_text(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("response"), str):
        return payload["response"]
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    if isinstance(payload.get("text"), str):
        return payload["text"]

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(_extract_text_part(part) for part in content)

    output = payload.get("output")
    if isinstance(output, list) and output:
        content = output[0].get("content", [])
        if isinstance(content, list):
            return "".join(_extract_text_part(part) for part in content)

    raise ProviderResponseError("Model response did not include text content")


def _extract_text_part(part: Any) -> str:
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        if isinstance(part.get("text"), str):
            return part["text"]
        if part.get("type") == "text" and isinstance(part.get("value"), str):
            return part["value"]
    return ""


def _parse_json_text(text: str) -> Any:
    candidates = [text.strip()]

    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        inner = "\n".join(stripped.splitlines()[1:-1]).strip()
        candidates.append(inner)

    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = stripped.find(open_char)
        end = stripped.rfind(close_char)
        if start != -1 and end != -1 and end > start:
            candidates.append(stripped[start : end + 1])

    for candidate in _dedupe(candidates):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ProviderResponseError("Model response did not contain valid JSON")


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _cache_key(kind: str, **parts: Any) -> str:
    suffix = ":".join(
        f"{name}={json.dumps(parts[name], ensure_ascii=True, sort_keys=True)}"
        for name in sorted(parts)
    )
    return f"clawhub:{kind}:{suffix}" if suffix else f"clawhub:{kind}"


def _normalize_skill_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    return _NON_ALNUM_RE.sub("-", normalized).strip("-")


def _slug_candidates(capability: str) -> List[str]:
    raw = capability.strip()
    candidates = [raw]
    if "/" in raw:
        candidates.insert(0, raw.rsplit("/", 1)[-1])

    normalized: List[str] = []
    seen = set()
    for candidate in candidates:
        slug = _normalize_skill_key(candidate)
        if slug and slug not in seen:
            normalized.append(slug)
            seen.add(slug)
    return normalized


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_clawhub_skill(
    detail: Dict[str, Any],
    *,
    search_result: Optional[Dict[str, Any]],
    skill_md: Optional[str],
) -> Dict[str, Any]:
    search_result = search_result or {}
    skill = detail.get("skill", {})
    latest_version = detail.get("latestVersion", {})
    metadata = detail.get("metadata", {})
    owner = detail.get("owner", {})

    slug = _coalesce(
        skill.get("slug") if isinstance(skill, dict) else None,
        search_result.get("slug"),
    )
    display_name = _coalesce(
        skill.get("displayName") if isinstance(skill, dict) else None,
        search_result.get("displayName"),
        slug,
    )
    return {
        "source": "clawhub",
        "slug": slug,
        "name": display_name,
        "display_name": display_name,
        "summary": _coalesce(
            skill.get("summary") if isinstance(skill, dict) else None,
            search_result.get("summary"),
        ),
        "version": _coalesce(
            latest_version.get("version")
            if isinstance(latest_version, dict)
            else None,
            search_result.get("version"),
        ),
        "search_score": search_result.get("score"),
        "skill_md": skill_md,
        "tags": skill.get("tags") if isinstance(skill, dict) else {},
        "stats": skill.get("stats") if isinstance(skill, dict) else {},
        "metadata": metadata if isinstance(metadata, dict) else {},
        "owner": owner if isinstance(owner, dict) else {},
        "moderation": detail.get("moderation"),
        "latest_version": latest_version if isinstance(latest_version, dict) else {},
    }


def _truncate(value: str, limit: int = 200) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
