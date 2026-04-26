---
layout: default
title: oidc-hunter
nav_order: 1
permalink: /
---

# oidc-hunter

`oidc-hunter` is an ADK-based application for discovering public OpenID Connect endpoints and maintaining a reviewable candidate set for JWKS Catalog inclusion.

The current implementation runs a bounded workflow with:

- durable state in `data/`
- live JWKS Catalog import on every run
- persisted `data/candidates.yaml` import and regeneration
- Cloudflare Radar top-domain seeding
- a `SequentialAgent` root workflow
- bounded investigation and candidate-review `LoopAgent`s
- a constrained investigator Python execution tool
- SQLite-backed run history, domain state, clusters, decisions, tactic scores, and lessons learned

Runtime configuration is supplied with environment variables:

| Variable | Purpose |
| --- | --- |
| `OIDC_HUNTER_STATE_DIR` | Directory for SQLite, candidates, reports, and artifacts. Defaults to `data`. |
| `OIDC_HUNTER_CATALOG_URL` | Live JWKS Catalog `services.yaml` URL. |
| `OIDC_HUNTER_LLM_BASE_URL` | OpenAI-compatible API base URL. |
| `OIDC_HUNTER_LLM_MODEL` | Model name passed to ADK/LiteLLM. |
| `OIDC_HUNTER_LLM_API_KEY` | Optional API key for the OpenAI-compatible endpoint. |
| `OIDC_HUNTER_LLM_TIMEOUT_SECONDS` | Per-request LiteLLM timeout. Defaults to `45`. |
| `OIDC_HUNTER_AGENTIC_TIMEOUT_SECONDS` | Global timeout for the ADK phase before deterministic fallback. Defaults to `90`. |
| `OIDC_HUNTER_CLOUDFLARE_API_TOKEN` | Cloudflare Radar API token. |
| `OIDC_HUNTER_CLOUDFLARE_DATASET_ALIAS` | Optional Radar dataset alias such as `ranking_top_1000`. |
| `OIDC_HUNTER_CLOUDFLARE_TOP_LIMIT` | Ordered Cloudflare top-domain limit. Defaults to `100`. |
| `OIDC_HUNTER_CLOUDFLARE_SEED_SAMPLE_SIZE` | Number of seed domains kept for one run. Defaults to `30`. |
| `OIDC_HUNTER_PROBE_DOMAINS` | Optional comma-separated fallback seed domains when Cloudflare is unavailable. |
| `OIDC_HUNTER_PROBE_TIMEOUT_SECONDS` | HTTP probe timeout. Defaults to `8`. |
| `OIDC_HUNTER_PROBE_CONCURRENCY` | Concurrent OIDC probes. Defaults to `8`. |
| `OIDC_HUNTER_INVESTIGATION_ITERATIONS` | Investigation loop iteration cap. Defaults to `3`. |
| `OIDC_HUNTER_REVIEW_ITERATIONS` | Candidate review loop iteration cap. Defaults to `20`. |
| `OIDC_HUNTER_INVESTIGATION_TARGET_LIMIT` | Total target budget across tactics. Defaults to `60`. |
| `OIDC_HUNTER_KEEP_PROBE_ARTIFACTS` | Set to `1` to keep probe artifacts for all results. |

The primary operator path is [`run.sh`](/Users/jaredhatfield/github/oidc-hunter/run.sh), which mounts [`data/`](/Users/jaredhatfield/github/oidc-hunter/data) into the container at `/data` and prefers the macOS `container` runtime before falling back to `docker` or `podman`.
