import pytest
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from skill_orchestrator.app import create_app
from skill_orchestrator.models import ResolutionStrategy
from skill_orchestrator.settings import Settings


@pytest.mark.asyncio
async def test_friendli_only_prototype_boots_and_resolves_with_local_fallbacks():
    friendli_calls = []
    clawhub_calls = []

    def friendli_handler(request: Request) -> Response:
        friendli_calls.append(request.url.path)
        if len(friendli_calls) == 1:
            return Response(
                200,
                json={"choices": [{"message": {"content": '{"unknown": true}'}}]},
            )
        return Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"draft": {"name": "summarize-pdf", "code": "pass", '
                                '"version": "0.1.0", "dependencies": []}}'
                            )
                        }
                    }
                ]
            },
        )

    def clawhub_handler(request: Request) -> Response:
        clawhub_calls.append(request.url.path)
        if request.url.path == "/api/v1/skills/summarize-pdf":
            return Response(404, json={"error": "not found"})
        if request.url.path == "/api/v1/search":
            return Response(200, json={"results": []})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    app = create_app(
        Settings(friendli_api_key="friendli-key"),
        transports={
            "friendli": MockTransport(friendli_handler),
            "clawhub": MockTransport(clawhub_handler),
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/resolve-skill-and-run",
            json={
                "capability": "summarize-pdf",
                "input_data": {"url": "doc.pdf"},
                "agent_id": "agent-1",
            },
        )

    data = response.json()
    assert data["success"] is True
    assert data["resolution_strategy"] == ResolutionStrategy.SYNTHESIS.value
    assert friendli_calls == ["/serverless/v1/chat/completions"]
    assert clawhub_calls == [
        "/api/v1/skills/summarize-pdf",
        "/api/v1/search",
        "/api/v1/search",
    ]
