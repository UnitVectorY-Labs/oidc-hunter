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
OIDC_HUNTER_AGENTIC_TIMEOUT_SECONDS=90 \
./run.sh
```
