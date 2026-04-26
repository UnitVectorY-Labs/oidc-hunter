---
layout: default
title: Tools
nav_order: 3
permalink: /tools
---

# Tools

The first implementation pass has deterministic internal tool functions rather than the full planned ADK tool surface. These functions are intentionally narrow so they can become ADK function tools later.

## Catalog Import

Imports the live JWKS Catalog `services.yaml` into SQLite for exclusion matching. The importer tolerates these OpenID configuration key variants:

- `openid-configuration`
- `openid_configuration`
- `open_id_configuration`

The catalog remains read-only input for this application.

## Candidate Import and Export

Loads `state/candidates.yaml` as the provisional known set. If the file is missing, the app initializes it with an empty candidate list.

After each run, active candidates are rendered deterministically from SQLite back to `state/candidates.yaml`.

## OIDC Probe

For each configured domain, the probe checks:

```text
https://<domain>/.well-known/openid-configuration
```

Valid responses must contain both `issuer` and `jwks_uri`. Probe summaries are stored in SQLite `domain_state`; raw response bodies are not stored.

## ADK Planning

When `OIDC_HUNTER_LLM_BASE_URL` and `OIDC_HUNTER_LLM_MODEL` are set, the app creates an ADK planning agent backed by an OpenAI-compatible LiteLLM model. The agent receives only the compact initialization summary and returns a bounded plan that is saved in SQLite and the run report.
