# AutoSkill

AutoSkill is a ClawHub/Claude skill backed by the `skill_orchestrator` service in `src/`. It detects capability gaps, searches for reusable skills, synthesizes new ones when needed, verifies trust and safety, and caches successful resolutions for reuse across agents.

## Quick Start

Install the skill package:

```bash
clawhub install PeytonLi/OCHack
```

Start a new Claude Code session. The SessionStart hook will launch the local service on port `8321`, expose the `auto-skill` command from `skills/auto-skill/SKILL.md`, and keep the service healthy through `GET /health`.

If you want real provider integrations, copy `.env.example` to `.env` and fill in the keys you want to enable. The packaged service can still start without those credentials by falling back to environment-backed stubs.

## Local Development

```bash
pip install -r requirements.txt
python -m pytest tests/ -v
python demo.py
bash scripts/start-service.sh start
bash scripts/start-service.sh status
bash scripts/start-service.sh stop
```

To run the API directly instead of using the hook:

```bash
PYTHONPATH=src uvicorn skill_orchestrator.app:app --host 127.0.0.1 --port 8321
```

## Configuration

Set `FRIENDLI_API_KEY` to enable production bootstrap. Additional providers are opt-in through `ENABLE_APIFY`, `ENABLE_CONTEXTUAL`, `ENABLE_CIVIC`, and `ENABLE_REDIS`; when a provider is disabled, the service falls back to local implementations where possible.

| Variable | Provider | Purpose |
|----------|----------|---------|
| `FRIENDLI_API_KEY` | Friendli | Capability gap detection, draft skill generation |
| `APIFY_API_TOKEN` | Apify | ClawHub search support, documentation crawling |
| `CONTEXTUAL_API_KEY` | Contextual AI | Grounded schema extraction, confidence scoring |
| `CIVIC_API_KEY` | Civic | Trust verification, policy authority |
| `REDIS_URL` | Redis | Cross-agent memory cache |

## API

```bash
curl -sf http://localhost:8321/health

curl -s -X POST http://localhost:8321/resolve-skill-and-run \
  -H "Content-Type: application/json" \
  -d '{"capability": "parse-csv", "input_data": {}, "agent_id": "claude-code"}'

curl -s http://localhost:8321/metrics
```

## Publish to ClawHub

The packaged skill manifest lives at `skills/auto-skill/SKILL.md`.

```bash
clawhub login
clawhub inspect .
clawhub publish .
```

## Architecture

```
POST /resolve-skill-and-run
         │
         ▼
  CapabilityRouter.resolve_and_run()
         │
         ├─ 1. CapabilityDetector (Friendli) - is this capability known?
         │     └─ known → return cached result
         │
         ├─ 2. SkillCache (Redis) - cross-agent memory lookup
         │     └─ hit → return cached result
         │
         ├─ 3. SkillRegistry (ClawHub) - retrieval search
         │     └─ found → TrustVerifier (Civic) gate → sandbox → return
         │
         └─ 4. SynthesisPipeline (no retrieval match)
               ├─ DocsCrawler (Apify) - crawl documentation
               ├─ GroundingProvider (Contextual AI) - extract schema + score
               ├─ CapabilityDetector (Friendli) - generate draft
               ├─ Confidence threshold check (stricter for high-risk)
               ├─ License allowlist check
               ├─ TrustVerifier (Civic) - hard block gate
               ├─ RuntimeSandbox - install, healthcheck, execute
               │     └─ healthcheck fail → publish as QUARANTINED
               └─ Publish as ACTIVE, cache in Redis

GET /health  → service readiness check
GET /metrics → telemetry counters
```

### Key Behaviors

1. Retrieval-first: local cache and ClawHub before synthesis
2. Civic hard block: trust verification failure blocks install, execute, and publish
3. Quarantine path: policy passes but smoke test fails -> published as quarantined
4. Retry with backoff: one automatic retry for transient verify/network failures
5. Partial success: capability-gap report with completed step results when resolution fails
6. High-risk thresholds: shell, network, filesystem, and exec skills require confidence >= 0.9
7. License allowlist: only MIT, Apache-2.0, BSD, ISC, Unlicense, and CC0 permitted
8. Global cache: Redis provides cross-agent memory reuse
