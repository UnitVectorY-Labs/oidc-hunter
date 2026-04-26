---
layout: default
title: Running
nav_order: 4
permalink: /running
---

# Running

The supported packaged entrypoint is [`run.sh`](/Users/jaredhatfield/github/oidc-hunter/run.sh).

## Runtime Selection

The script chooses runtimes in this order:

1. macOS `container`
2. `docker`
3. `podman`

If none are available, the script exits with an error.

## Persistent State

`run.sh` mounts [`data/`](/Users/jaredhatfield/github/oidc-hunter/data) into the container as `/data`.

That mounted directory holds:

- `oidc-hunter.db`
- `candidates.yaml`
- `reports/`
- `lessons/`
- `artifacts/runs/<run_id>/...`

This lets sequential runs reuse prior exclusions, domain history, tactic scores, and lessons learned.

## Environment Normalization

The repo currently carries a `.env` file with these legacy names:

- `OPENAPI_BASE_URL`
- `MODEL`
- `CLOUDFLARE_API_KEY`

`run.sh` maps them to:

- `OIDC_HUNTER_LLM_BASE_URL`
- `OIDC_HUNTER_LLM_MODEL`
- `OIDC_HUNTER_CLOUDFLARE_API_TOKEN`

Set `OIDC_HUNTER_SKIP_DOTENV=1` to prevent `run.sh` from sourcing `.env`.

The application itself also honors `OIDC_HUNTER_SKIP_DOTENV=1`, so direct local runs like `.venv/bin/python -m oidc_hunter` can bypass `.env` as well.

## Runtime Logs

The application now prints concise progress markers for:

- initialization
- planning
- investigation batch generation and probing
- candidate review decisions
- finalization

This makes it much easier to tell whether a slower local model is still working, whether a stage has timed out, and when the workflow has switched to deterministic fallback.

## ADK Fallback Behavior

`OIDC_HUNTER_AGENTIC_TIMEOUT_SECONDS` is the total ADK budget for one run. The default is `180`, and the app currently splits that budget across:

- planning
- investigation
- review

If a stage times out, the app falls back only for that stage instead of rerunning the whole workflow from scratch. Reports record the fallback stages in a structured `LLM Fallback` section.

## Useful Overrides

```bash
OIDC_HUNTER_INVESTIGATION_ITERATIONS=1 \
OIDC_HUNTER_REVIEW_ITERATIONS=1 \
OIDC_HUNTER_CLOUDFLARE_TOP_LIMIT=10 \
OIDC_HUNTER_CLOUDFLARE_SEED_SAMPLE_SIZE=5 \
./run.sh
```

```bash
OIDC_HUNTER_LLM_TIMEOUT_SECONDS=30 \
OIDC_HUNTER_AGENTIC_TIMEOUT_SECONDS=180 \
./run.sh
```

```bash
OIDC_HUNTER_SKIP_DOTENV=1 \
OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-local \
.venv/bin/python -m oidc_hunter
```
