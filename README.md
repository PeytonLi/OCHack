# AutoSkill

```bash
clawhub install PeytonLi/OCHack
```

AutoSkill is a self-evolving skill orchestrator for Claude Code. When an agent encounters a capability gap — a failed tool call, unknown domain, or missing functionality — AutoSkill automatically discovers existing skills in ClawHub, synthesizes new ones from documentation, validates trust and safety through Civic, and executes them in a sandbox. Resolved skills are cached in Redis for cross-agent reuse.

## Quick Start

1. **Install the plugin:**
   ```bash
   clawhub install PeytonLi/OCHack
   ```

2. **Set environment variables** (optional — works without them using in-memory defaults):
   ```bash
   export FRIENDLI_API_KEY="..."
   export APIFY_API_TOKEN="..."
   export CONTEXTUAL_API_KEY="..."
   export CIVIC_API_KEY="..."
   export REDIS_URL="redis://localhost:6379"
   ```

3. **Start a new Claude Code session.** The SessionStart hook automatically launches the AutoSkill service on port 8321. You'll see a confirmation message when it's ready.

4. **Use it.** AutoSkill activates automatically when capability gaps are detected, or invoke manually with `/auto-skill`.

## Configuration

| Variable | Provider | Purpose | Default behavior when missing |
|----------|----------|---------|-------------------------------|
| `FRIENDLI_API_KEY` | Friendli | Capability gap detection, draft skill generation | Assumes all capabilities are unknown |
| `APIFY_API_TOKEN` | Apify | ClawHub search, docs crawling | Search and crawling disabled |
| `CONTEXTUAL_API_KEY` | Contextual AI | Grounded schema extraction, confidence scoring | Returns empty schema with 0.0 confidence |
| `CIVIC_API_KEY` | Civic | Trust verification (hard block authority) | Allows all skills (permissive) |
| `REDIS_URL` | Redis | Cross-agent memory cache | In-memory cache (not shared across agents) |
| `AUTOSKILL_PORT` | — | Service port | `8321` |

## Manual Invocation

Use `/auto-skill` in Claude Code, or call the API directly:

```bash
curl -s -X POST http://localhost:8321/resolve-skill-and-run \
  -H "Content-Type: application/json" \
  -d '{"capability": "parse-csv", "input_data": {}, "agent_id": "claude-code"}'
```

Check service health:

```bash
curl -sf http://localhost:8321/health
```

View telemetry:

```bash
curl -s http://localhost:8321/metrics
```

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests (all 24 run with in-memory fakes — no external services required)
python -m pytest tests/ -v

# Run the demo (6 deterministic scenarios)
python demo.py

# Start the service manually
bash scripts/start-service.sh start

# Check service status / stop
bash scripts/start-service.sh status
bash scripts/start-service.sh stop
```

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

GET /health  → service readiness check
GET /metrics → telemetry counters
```

### Key Behaviors

1. **Retrieval-first**: Local cache and ClawHub before synthesis
2. **Civic hard block**: Trust verification failure blocks install/execute/publish
3. **Quarantine path**: Policy passes but smoke test fails → published as quarantined
4. **Retry with backoff**: One automatic retry for transient verify/network failures
5. **Partial success**: Capability-gap report with completed step results when resolution fails
6. **High-risk thresholds**: Shell/network/filesystem skills require confidence >= 0.9
7. **License allowlist**: Only MIT, Apache-2.0, BSD, ISC, Unlicense, CC0 permitted
8. **Global cache**: Redis provides cross-agent memory reuse

## Publish

```bash
clawhub login
clawhub inspect .
clawhub publish .
```

## License

MIT
