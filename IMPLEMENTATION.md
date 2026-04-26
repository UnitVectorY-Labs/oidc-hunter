# Implementation Progress

## Completed

- [x] Added a first-pass Python package under `src/oidc_hunter`.
- [x] Added a managed-Dockerfile-compatible `src/main.py` entrypoint.
- [x] Added environment-driven configuration for state paths, catalog URL, OpenAI-compatible LLM base URL, model name, API key, and optional probe domains.
- [x] Added SQLite initialization with first-pass tables for runs, catalog entries, candidate entries, domain state, run plans, and candidate decisions.
- [x] Added catalog and `candidates.yaml` importers that tolerate the key variants called out in `DESIGN.md`.
- [x] Added deterministic OIDC probing for configured domains using `https://<domain>/.well-known/openid-configuration`.
- [x] Added deterministic candidate export to `state/candidates.yaml`.
- [x] Added a first ADK planning step that uses an OpenAI-compatible LiteLLM model only from environment-provided settings.
- [x] Added durable per-run markdown reports under `state/reports`.
- [x] Added basic importer tests.
- [x] Verified local compile and unit tests with Python 3.14.
- [x] Verified local no-LLM smoke run creates state, candidates, and a report.
- [x] Verified ADK/LiteLLM planning against the OpenAI-compatible development endpoint using environment-provided URL and model values.
- [x] Verified `container build -t oidc-hunter:dev .` succeeds with the macOS `container` command.
- [x] Verified `container run --rm --env OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-state oidc-hunter:dev` starts and completes.

## Outstanding

- [ ] Replace the first-pass linear orchestration with the full `SequentialAgent` workflow described in `PLAN.md`.
- [ ] Add the bounded investigation Python execution tool.
- [ ] Add Cloudflare top-domain ingestion and sampling.
- [ ] Add richer known-set matching by issuer, OpenID configuration URL, JWKS URI, primary domain, and aliases.
- [ ] Add candidate clustering and alternative-domain handling.
- [ ] Add review loops for candidate analysis and candidate decisions.
- [ ] Add strategy tactic scoring, coverage summaries, and lessons learned.
- [ ] Harden sandboxing, timeouts, and artifact retention for agent-authored investigation code.
- [ ] Add integration tests with mocked HTTP endpoints and mocked ADK/LiteLLM responses.

## Next Steps

1. Introduce Cloudflare input ingestion and make the ADK planning output select a small deterministic probe batch.
2. Expand candidate export semantics so prior candidates, newly discovered candidates, and rejected findings are represented with clearer statuses.
3. Convert the current deterministic functions into explicit ADK function tools for the planned `SequentialAgent`.
4. Add integration tests with mocked catalog, candidate, OIDC discovery, and LLM endpoints.

## Verification Notes

- Local unit tests: `.venv/bin/python -m unittest discover`
- Local no-LLM smoke: `OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-smoke2 .venv/bin/python -m oidc_hunter`
- Local ADK smoke: `OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-llm-smoke2 OIDC_HUNTER_LLM_BASE_URL=https://llm.unitvectory-labs.net/v1 OIDC_HUNTER_LLM_MODEL=gemma4-31b-it-q5kxl-instruct .venv/bin/python -m oidc_hunter`
- Container build: `container build -t oidc-hunter:dev .`
- Container run: `container run --rm --env OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-state oidc-hunter:dev`

## Managed File Note

`pyproject.toml` is listed in `.managedfiles`. It was updated in this first pass to add required application dependencies. The upstream gitrepoforge template or external configuration should be updated so these dependency changes are preserved the next time gitrepoforge runs.
