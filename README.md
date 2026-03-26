# AutoSkill

AutoSkill is a ClawHub/Claude skill backed by the `skill_orchestrator` service in `src/`. The production path uses Friendli for capability-gap detection and draft generation, ClawHub for registry search and `SKILL.md` retrieval, Redis for cross-agent caching, and a `clawhub` CLI-backed runtime sandbox for install and execution.

## Quick Start

Install the skill package:

```bash
clawhub install PeytonLi/OCHack
```

Start a new Claude Code session. The SessionStart hook launches the local service on port `8321`, exposes the `auto-skill` command from `skills/auto-skill/SKILL.md`, and keeps the service healthy through `GET /health`.

## Local Development

```bash
pip install -r requirements.txt
python -m pytest -q
python demo.py
bash scripts/start-service.sh start
bash scripts/start-service.sh status
bash scripts/start-service.sh stop
```

To run the API directly:

```bash
PYTHONPATH=src uvicorn skill_orchestrator.app:app --host 127.0.0.1 --port 8321
```

## Configuration

Production mode requires a valid Friendli key and a working `clawhub` CLI on the host. ClawHub registry search and `SKILL.md` retrieval use the public registry directly. When Redis is enabled, both skill results and ClawHub payload downloads are cached for reuse across agents.

| Variable | Purpose |
|----------|---------|
| `FRIENDLI_API_KEY` | Friendli capability-gap detection and draft generation |
| `FRIENDLI_BASE_URL` | Friendli API base URL |
| `FRIENDLI_MODEL` | Friendli model used for detection and draft generation |
| `CLAWHUB_BASE_URL` | ClawHub registry API base URL |
| `CLAWHUB_CACHE_TTL_SECONDS` | TTL for cached ClawHub search/detail/file payloads |
| `CLAWHUB_BIN` | `clawhub` CLI binary name or path |
| `CLAWHUB_SEARCH_LIMIT` | Max ClawHub search results for retrieval |
| `CLAWHUB_DOCS_LIMIT` | Max ClawHub docs payloads used for synthesis |
| `REDIS_URL` | Redis URL for cross-agent caching |
| `ENABLE_REDIS` | Enable Redis result and payload caches |
| `SKILL_CACHE_TTL_SECONDS` | TTL for executed skill results |
| `SANDBOX_ROOT` | Root directory for per-request runtime workspaces |
| `EXECUTION_TIMEOUT_SECONDS` | Timeout for `clawhub` install and skill execution |
| `ENABLE_APIFY` | Optional legacy docs fallback after ClawHub docs lookup |
| `APIFY_API_TOKEN` | Optional Apify token for the legacy docs fallback |

## API

```bash
curl -sf http://localhost:8321/health

curl -s -X POST http://localhost:8321/resolve-skill-and-run \
  -H "Content-Type: application/json" \
  -d '{"capability": "parse-csv", "input_data": {}, "agent_id": "claude-code"}'

curl -s http://localhost:8321/metrics
```

`POST /resolve-skill-and-run` returns:

- `native_capability` when Friendli says the capability is already available in the current toolset
- `local_cache` when Redis has a prior executed result
- `clawhub_retrieval` when a published ClawHub skill is installed and executed
- `synthesis` when Friendli generates a runnable draft package that executes successfully

`publish_state` remains in the response model for compatibility but is not used in the main production flow.

## Architecture

```text
POST /resolve-skill-and-run
  -> Friendli detect_gap()
    -> known capability -> native_capability response
    -> unknown capability
       -> Redis result cache lookup
       -> ClawHub registry search
          -> hit -> clawhub install -> local healthcheck -> run hook -> cache result
          -> miss
             -> ClawHub docs crawl (optional Apify fallback)
             -> Friendli generate_draft()
             -> validate generated package contract
             -> license allowlist check
             -> materialize local package
             -> local healthcheck -> run hook -> cache result
```

## Notes

- The runtime sandbox is Linux-first, but the codebase keeps basic Windows compatibility for local development and tests.
- Generated drafts must include `name`, `description`, and runnable `SKILL.md` content, either directly as `skill_md` or inside a `files["SKILL.md"]` entry.
- Redis failures degrade to no-cache behavior; they do not fail the request.
