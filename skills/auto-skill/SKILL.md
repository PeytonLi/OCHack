---
name: auto-skill
description: Use when the agent encounters a capability gap -- a failed tool call, unknown domain, missing functionality, or the user asks for something outside current tools. Automatically discovers, generates, validates, and executes skills.
metadata:
  openclaw:
    requires:
      bins:
        - python
    config:
      optionalEnv:
        - FRIENDLI_API_KEY
        - APIFY_API_TOKEN
        - CONTEXTUAL_API_KEY
        - CIVIC_API_KEY
        - REDIS_URL
---
# AutoSkill

Resolve unknown capabilities by discovering, validating, and executing skills on-the-fly.

## When to Use

Invoke `/auto-skill` when you detect any of these situations:
- A tool call fails because the capability does not exist
- The user asks for functionality outside your current tool set
- You encounter a domain you have no built-in knowledge of
- You think "I can't do X" -- AutoSkill may be able to resolve X

## How It Works

AutoSkill runs a local background service that:
1. Checks a cross-agent cache (Redis) for previously resolved skills
2. Searches ClawHub for published skills matching the capability
3. If nothing found, synthesizes a new skill from documentation
4. Validates trust (Civic), licenses, and confidence thresholds
5. Sandboxes, executes, and caches the result for reuse

## Usage

### Step 1: Check Service Health

```bash
curl -sf http://localhost:8321/health || echo "AutoSkill service not running -- start it with: bash scripts/start-service.sh start"
```

If the service is not running, start it:
```bash
bash scripts/start-service.sh start
```

### Step 2: Resolve and Execute

Send a POST request with the capability you need:

```bash
curl -s -X POST http://localhost:8321/resolve-skill-and-run \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "<describe-the-capability>",
    "input_data": {},
    "agent_id": "claude-code"
  }'
```

Replace `<describe-the-capability>` with a short slug describing what you need (e.g., `parse-csv`, `summarize-pdf`, `translate-text`).

### Step 3: Interpret the Response

The response is JSON with these fields:

| Field | Meaning |
|-------|---------|
| `success` | `true` if the capability was resolved and executed |
| `capability` | The capability that was requested |
| `result` | The execution output (if successful) |
| `resolution_strategy` | How it was resolved: `local_cache`, `clawhub_retrieval`, or `synthesis` |
| `publish_state` | `active` or `quarantined` (quarantined means healthcheck failed) |
| `capability_gaps` | List of unresolved gaps with reasons (if partially failed) |
| `error` | Error message (if failed) |

### Step 4: Handle Outcomes

- **success=true, publish_state=active**: Use the `result` directly.
- **success=true, publish_state=quarantined**: The skill was generated but its healthcheck failed. Treat the result with caution and inform the user.
- **success=false, error contains "trust"**: The skill was blocked by Civic trust verification. Do not retry -- this is a hard policy block.
- **success=false, capability_gaps present**: Partial results are in `result` (docs, schema). Report the gap to the user with context from the gap report.
- **Service not running**: Fall back to informing the user that AutoSkill is available but needs to be started.

## Configuration

AutoSkill works with zero configuration using in-memory fakes for development. For production use with real providers, set these environment variables:

| Variable | Provider | Purpose |
|----------|----------|---------|
| `FRIENDLI_API_KEY` | Friendli | Capability gap detection, draft skill generation |
| `APIFY_API_TOKEN` | Apify | ClawHub search, docs crawling |
| `CONTEXTUAL_API_KEY` | Contextual AI | Grounded schema extraction, confidence scoring |
| `CIVIC_API_KEY` | Civic | Trust verification (hard block authority) |
| `REDIS_URL` | Redis | Cross-agent memory cache |

## Safety

- Civic trust verification is a **hard block** -- if verification fails, the skill is never installed or executed
- High-risk capabilities (shell, network, filesystem, exec) require confidence >= 0.9
- Only permissive licenses are allowed (MIT, Apache-2.0, BSD, ISC, Unlicense, CC0)
- Failed sandbox healthchecks result in quarantined publish state, not execution
