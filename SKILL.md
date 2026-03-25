---
name: OCHack
description: Resolve unknown capabilities by discovering, validating, publishing, and executing skills with trust gating and cross-agent memory.
metadata:
  openclaw:
    requires:
      env:
        - FRIENDLI_API_KEY
        - APIFY_API_TOKEN
        - CONTEXTUAL_API_KEY
        - CIVIC_API_KEY
        - REDIS_URL
      bins:
        - python
    primaryEnv: FRIENDLI_API_KEY
    config:
      requiredEnv:
        - FRIENDLI_API_KEY
        - APIFY_API_TOKEN
        - CONTEXTUAL_API_KEY
        - CIVIC_API_KEY
        - REDIS_URL
---
# Skill Orchestrator

Use this skill when an agent request needs capabilities not currently available in the local tool registry.

## What It Does
- Detects capability gaps
- Searches existing skills first
- Falls back to docs grounding and draft generation
- Enforces trust verification before install, execute, and publish
- Reuses resolved skills through short-term shared memory

## Runtime Requirements
- Python 3.9+
- Dependencies from requirements.txt
- Environment variables listed in frontmatter

## Typical Flow
1. Request arrives with unknown capability.
2. Retrieval path checks cache and registry.
3. If unresolved, docs are crawled and grounded.
4. Draft skill is generated and policy-validated.
5. Skill is published as active or quarantined.
6. Execution result is returned, or partial success if blocked.

## Safety Model
- Civic verification is a hard block.
- High-risk capabilities use stricter thresholds.
- Failed smoke tests produce quarantined publish state.
