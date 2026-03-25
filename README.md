# Skill Orchestrator

Self-evolving skill orchestration service. An agent can detect missing capabilities, discover or generate skills, validate trust/safety, publish, execute, and reuse skills across agents.

## Setup

```bash
pip install -r requirements.txt
```

## Required Environment Variables

For production use, configure these provider credentials:

| Variable | Provider | Purpose |
|----------|----------|---------|
| `FRIENDLI_API_KEY` | Friendli | Capability gap detection, draft skill generation |
| `APIFY_API_TOKEN` | Apify | ClawHub search support, docs crawling fallback |
| `CONTEXTUAL_API_KEY` | Contextual AI | Grounded schema extraction, confidence scoring |
| `CIVIC_API_KEY` | Civic | Trust verification, policy authority |
| `REDIS_URL` | Redis | Short-term cross-agent memory (default: `redis://localhost:6379`) |

For development/testing, all providers are injected as fakes — no env vars needed.

## Run

```bash
uvicorn skill_orchestrator.app:app --host 0.0.0.0 --port 8000
```

Note: Production use requires configuring `set_adapters()` with real provider implementations at startup.

## Publish to ClawHub

```bash
clawhub login
clawhub inspect .
clawhub publish .
```

After publish succeeds, install from another machine or agent environment:

```bash
clawhub install <your-skill-slug>
```

To verify installation quickly, run:

```bash
python demo.py
```

## Test

```bash
python -m pytest tests/ -v
```

All 24 tests run with in-memory fakes — no external services required.

## Architecture

```
POST /resolve-skill-and-run
         │
         ▼
  CapabilityRouter.resolve_and_run()
         │
         ├─ 1. CapabilityDetector (Friendli) — is this capability known?
         │     └─ known → return cached result
         │
         ├─ 2. SkillCache (Redis) — cross-agent memory lookup
         │     └─ hit → return cached result
         │
         ├─ 3. SkillRegistry (ClawHub) — retrieval search
         │     └─ found → TrustVerifier (Civic) gate → sandbox → return
         │
         └─ 4. SynthesisPipeline (no retrieval match)
               ├─ DocsCrawler (Apify) — crawl documentation
               ├─ GroundingProvider (Contextual AI) — extract schema + score
               ├─ CapabilityDetector (Friendli) — generate draft
               ├─ Confidence threshold check (stricter for high-risk)
               ├─ License allowlist check
               ├─ TrustVerifier (Civic) — hard block gate
               ├─ RuntimeSandbox — install, healthcheck, execute
               │     └─ healthcheck fail → publish as QUARANTINED
               └─ Publish as ACTIVE, cache in Redis

GET /metrics → telemetry counters
```

### Modules

| Module | Responsibility |
|--------|---------------|
| `CapabilityRouter` | Orchestrates the full resolution flow |
| `DiscoveryEngine` | Retrieval-first strategy (cache → ClawHub) |
| `GroundingEngine` | Schema extraction and confidence scoring (Contextual AI) |
| `TrustEngine` | Civic verification — hard block authority |
| `SynthesisPipeline` | Docs → draft → validate → publish chain |
| `RuntimeSandbox` | Sandbox install, healthcheck, execute, rollback |
| `SkillMemory` | Redis-backed cross-agent cache |
| `PublishEngine` | Active/quarantined state transitions |
| `TelemetryAudit` | MTTC, block rate, quarantine count, cache-hit ratio |

### Key Behaviors

1. **Retrieval-first**: Local cache and ClawHub before synthesis
2. **Civic hard block**: Trust verification failure blocks install/execute/publish
3. **Quarantine path**: Policy passes but smoke test fails → published as quarantined
4. **Retry with backoff**: One automatic retry for transient verify/network failures
5. **Partial success**: Capability-gap report with completed step results when resolution fails
6. **High-risk thresholds**: Shell/network/filesystem skills require confidence >= 0.9
7. **License allowlist**: Only MIT, Apache-2.0, BSD, ISC, Unlicense, CC0 permitted
8. **Global cache**: Redis provides cross-agent memory reuse for MVP
