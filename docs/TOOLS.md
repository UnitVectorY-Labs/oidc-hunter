---
layout: default
title: Tools
nav_order: 3
permalink: /tools
---

# Tools

The application now exposes the planned workflow through a deterministic tool layer that the ADK agents call.

## Initialization Tools

- live catalog fetch and import
- `candidates.yaml` import and snapshot recording
- Cloudflare Radar batch fetch
- tactic table initialization

The catalog importer tolerates these OpenID configuration key variants:

- `openid-configuration`
- `openid_configuration`
- `open_id_configuration`

## Planning Tools

- `load_cloudflare_batch_metadata`
- `sample_candidate_tlds`
- `load_heuristics_library`
- `record_run_plan`

These tools keep full Cloudflare data out of prompt context and return only compact samples plus artifact references.

## Investigation Tools

- `load_plan_batch`
- `load_domain_history_summary`
- `load_known_issuers_summary`
- `execute_investigation_python`
- `record_investigation_output`

`execute_investigation_python` runs investigator-authored code inside a bounded subprocess with:

- a restricted builtin set
- no import support
- run-scoped input and output files only
- a hard subprocess timeout

If the generated code fails or produces no useful targets, the application falls back to built-in tactic generation.

## Verification Tools

- `probe_oidc_candidates`
- `load_investigation_progress`
- `mark_tactic_outcome`

Each probe checks:

```text
https://<domain>/.well-known/openid-configuration
```

Valid responses are clustered by issuer and JWKS URI. The system persists probe summaries, domain state, issuer clusters, and known-set matches in SQLite.

## Review Tools

- `load_next_candidate_cluster`
- `load_cluster_evidence`
- `record_analysis_notes`
- `mark_cluster_rejected`
- `mark_cluster_for_followup`
- `promote_cluster`

Promotion either creates a new active candidate or merges additional domains into an existing active candidate's alias list.

Transient workflow cursor keys for investigation and review are cleared in a way that works with both normal Python dictionaries and ADK session-state wrappers that only support reads and writes. This prevents cluster decisions and tactic completion from failing during state cleanup.

## Export and Reporting Tools

- `load_candidates_for_export`
- `write_candidates_yaml`
- `load_run_outcome_summary`
- `append_lessons_learned`
- `update_strategy_scores`
- `write_run_report`
- `close_current_run`

The final `candidates.yaml` is rendered deterministically from SQLite. The LLM never patches that file directly.
