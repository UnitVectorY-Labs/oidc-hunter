---
layout: default
title: oidc-hunter
nav_order: 1
permalink: /
---

# oidc-hunter

`oidc-hunter` is a first-pass ADK-based application for discovering public OpenID Connect endpoints and maintaining a reviewable candidate set for JWKS Catalog inclusion.

The current implementation is an initial vertical slice:

- creates durable local state in `state/`
- imports the live JWKS Catalog as an exclusion source when reachable
- imports or initializes `state/candidates.yaml`
- optionally probes domains from `OIDC_HUNTER_PROBE_DOMAINS`
- asks an ADK planning agent for a bounded next-run plan when LLM settings are configured
- writes a run report to `state/reports/`

Runtime configuration is supplied with environment variables:

| Variable | Purpose |
| --- | --- |
| `OIDC_HUNTER_STATE_DIR` | Directory for SQLite, candidates, reports, and artifacts. Defaults to `state`. |
| `OIDC_HUNTER_CATALOG_URL` | Live JWKS Catalog `services.yaml` URL. |
| `OIDC_HUNTER_LLM_BASE_URL` | OpenAI-compatible API base URL. |
| `OIDC_HUNTER_LLM_MODEL` | Model name passed to ADK/LiteLLM. |
| `OIDC_HUNTER_LLM_API_KEY` | Optional API key for the OpenAI-compatible endpoint. |
| `OIDC_HUNTER_PROBE_DOMAINS` | Optional comma-separated domains to probe in this first pass. |
| `OIDC_HUNTER_PROBE_TIMEOUT_SECONDS` | HTTP probe timeout. Defaults to `8`. |

The endpoint URL and model name are intentionally not hard coded. For the development endpoint, set `OIDC_HUNTER_LLM_BASE_URL` and choose the desired model through `OIDC_HUNTER_LLM_MODEL`.
