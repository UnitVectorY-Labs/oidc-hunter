# Implementation Progress

## Completed

- [x] Added the first-pass Python package under `src/oidc_hunter`.
- [x] Added a managed-Dockerfile-compatible `src/main.py` entrypoint.
- [x] Added environment-driven configuration for state paths, catalog URL, OpenAI-compatible LLM base URL, model name, API key, optional Cloudflare configuration, and bounded runtime controls.
- [x] Moved the default durable state layout to `data/` with SQLite, `candidates.yaml`, reports, lessons, and per-run artifacts.
- [x] Expanded SQLite initialization to include snapshots, tactic metadata, investigation batches, probe summaries, issuer clusters, decisions, and lessons learned.
- [x] Added catalog and `candidates.yaml` importers that tolerate the key variants called out in `DESIGN.md`.
- [x] Added Cloudflare Radar top-domain ingestion and persisted seed-batch artifacts.
- [x] Added deterministic OIDC probing for candidate domains using `https://<domain>/.well-known/openid-configuration`.
- [x] Added issuer clustering, known-set matching, candidate promotion, alternative-domain handling, and deterministic candidate export.
- [x] Added a `SequentialAgent` root workflow with bounded investigation and review `LoopAgent`s.
- [x] Added the bounded investigator Python execution tool with subprocess isolation and fallback tactic generation.
- [x] Added durable per-run markdown reports, lessons learned, and tactic score updates under `data/`.
- [x] Added importer and end-to-end deterministic workflow tests.
- [x] Verified local compile and unit tests with Python 3.14.
- [x] Verified local no-LLM smoke run creates state, candidates, lessons, and a report.
- [x] Verified `container build -t oidc-hunter:dev .` succeeds with the macOS `container` command.
- [x] Added `run.sh` to select `container`, then `docker`, then `podman`, while mounting `data/` as persistent state.

## Outstanding

- [ ] Tighten the live agentic path so slower local-model endpoints complete more often before the deterministic fallback is needed.
- [ ] Add mocked ADK/LiteLLM agent-path integration coverage rather than only deterministic workflow coverage.

## Next Steps

1. Reduce prompt and tool-call overhead in the live ADK path so bounded runs finish faster against the configured local model endpoint.
2. Add ADK-path tests that stub LiteLLM responses and verify loop progression without depending on the external model server.
3. Expand review heuristics for suspicious issuer-domain relationships and candidate staleness handling.
4. Add richer retained probe artifacts for ambiguous-but-interesting findings.

## Verification Notes

- Local unit tests: `.venv/bin/python -m unittest discover`
- Local no-LLM smoke: `OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-local-smoke3 .venv/bin/python -m oidc_hunter`
- Container build: `container build -t oidc-hunter:dev .`
- Packaged run wrapper: `./run.sh`
- Live packaged verification: catalog and Cloudflare fetches succeeded inside the mounted `data/` workflow; the resulting artifacts were used to fix Cloudflare response parsing and add explicit agentic timeouts plus deterministic fallback.

## Managed File Note

`pyproject.toml` is listed in `.managedfiles`. It was updated in this first pass to add required application dependencies. The upstream gitrepoforge template or external configuration should be updated so these dependency changes are preserved the next time gitrepoforge runs.
