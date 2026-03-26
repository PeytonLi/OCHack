---
name: auto-skill
description: Use when the agent encounters a capability gap -- a failed tool call, unknown domain, missing functionality, or the user asks for something outside current tools. Automatically discovers, generates, installs, and executes skills.
metadata:
  openclaw:
    requires:
      bins:
        - python
        - clawhub
    config:
      optionalEnv:
        - FRIENDLI_API_KEY
        - CLAWHUB_BASE_URL
        - REDIS_URL
        - APIFY_API_TOKEN
---
# AutoSkill

Resolve unknown capabilities by discovering, validating, installing, and executing skills on demand.

## How It Works

AutoSkill runs a local background service that:
1. Asks Friendli whether the requested capability is already native
2. Checks Redis for a previously executed result
3. Searches ClawHub for a published skill and installs it with the `clawhub` CLI
4. If nothing matches, gathers ClawHub docs and asks Friendli to generate a runnable draft package
5. Runs a local healthcheck and execution hook inside an isolated workspace
6. Caches successful results for reuse across agents

## Usage

### Step 1: Check Service Health

```bash
curl -sf http://localhost:8321/health || echo "AutoSkill service not running -- start it with: bash scripts/start-service.sh start"
```

### Step 2: Resolve and Execute

```bash
curl -s -X POST http://localhost:8321/resolve-skill-and-run \
  -H "Content-Type: application/json" \
  -d '{
    "capability": "<describe-the-capability>",
    "input_data": {},
    "agent_id": "claude-code"
  }'
```

### Step 3: Interpret the Response

| Field | Meaning |
|-------|---------|
| `success` | `true` if the capability resolved and executed successfully |
| `capability` | The requested capability |
| `result` | Execution output or native-capability status |
| `resolution_strategy` | `native_capability`, `local_cache`, `clawhub_retrieval`, or `synthesis` |
| `capability_gaps` | Gap report when synthesis could not produce a runnable draft |
| `error` | Failure message |

`publish_state` may appear for backward compatibility, but the main production path does not use it.

## Configuration

| Variable | Purpose |
|----------|---------|
| `FRIENDLI_API_KEY` | Capability-gap detection and runnable draft generation |
| `CLAWHUB_BASE_URL` | ClawHub registry API base URL |
| `CLAWHUB_BIN` | `clawhub` CLI binary name or path |
| `REDIS_URL` | Redis URL for cross-agent result reuse |
| `ENABLE_REDIS` | Enable Redis caches |
| `SANDBOX_ROOT` | Root directory for isolated runtime workspaces |
| `EXECUTION_TIMEOUT_SECONDS` | Timeout for install and execution |
| `APIFY_API_TOKEN` | Optional legacy docs fallback |

## Safety

- Generated drafts must include `name`, `description`, and runnable `SKILL.md` content
- Only permissive dependency licenses are allowed
- Runtime failures return errors; they are not published or quarantined in the main production flow
- Redis failures degrade to no-cache behavior rather than failing the request
