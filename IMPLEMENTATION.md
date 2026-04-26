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
- [x] Tightened the live ADK path by reducing loop model turns, adding stage-scoped timeouts, durable-state-aware fallback, and explicit progress logging.
- [x] Added mocked ADK/LiteLLM stage-path coverage rather than only deterministic workflow coverage.
- [x] Verified local compile and unit tests with Python 3.14.
- [x] Verified local no-LLM smoke run creates state, candidates, lessons, and a report.
- [x] Verified `container build -t oidc-hunter:dev .` succeeds with the macOS `container` command.
- [x] Added `run.sh` to select `container`, then `docker`, then `podman`, while mounting `data/` as persistent state.
- [x] Verified real live-model runs after the ADK refactor, including a packaged `run.sh` execution against mounted `data/` and a host-side run that exposed and fixed ADK `State` mutation issues.

## Outstanding

- None in the current implementation checklist.

## Next Steps

1. Expand review heuristics for suspicious issuer-domain relationships and candidate staleness handling.
2. Add richer retained probe artifacts for ambiguous-but-interesting findings.
3. Investigate why the current local endpoint still times out some ADK stages even with the larger default budget, and decide whether further prompt simplification or endpoint-side tuning is warranted.

## Verification Notes

- Local unit tests: `.venv/bin/python -m unittest discover`
- Local no-LLM smoke: `OIDC_HUNTER_SKIP_DOTENV=1 OIDC_HUNTER_STATE_DIR=/tmp/oidc-hunter-local-verify-2 .venv/bin/python -m oidc_hunter`
- Container build: `container build -t oidc-hunter:dev .`
- Packaged run wrapper: `./run.sh`
- Live packaged verification: on 2026-04-26, `./run.sh` fetched the live catalog and Cloudflare seeds into mounted `data/`, persisted SQLite state plus artifacts, and completed with stage-scoped ADK fallback instead of rerunning the whole workflow.
- Live host-side verification: on 2026-04-26, a direct `.venv/bin/python -m oidc_hunter` run against the configured local model surfaced an ADK `tool_context.state` mutation bug, which was fixed by replacing `.pop()` calls with key-safe deletion.

## Managed File Note

`pyproject.toml` is listed in `.managedfiles`. It was updated in this first pass to add required application dependencies. The upstream gitrepoforge template or external configuration should be updated so these dependency changes are preserved the next time gitrepoforge runs.
