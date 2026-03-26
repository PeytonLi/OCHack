from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

import httpx

from skill_orchestrator.exceptions import (
    ProviderAuthError,
    ProviderResponseError,
    TransientProviderError,
)


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

    async def _request_json(self, method: str, path: str, **kwargs) -> Any:
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

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderResponseError(
                f"{self.provider_name} returned invalid JSON"
            ) from exc

    async def _post_json(self, path: str, payload: Dict[str, Any], **kwargs) -> Any:
        return await self._request_json("POST", path, json=payload, **kwargs)

    def _extract_llm_json(self, payload: Dict[str, Any]) -> Any:
        text = _extract_text(payload)
        return _parse_json_text(text)


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


def _truncate(value: str, limit: int = 200) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
