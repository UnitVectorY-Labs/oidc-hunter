# OIDC Finder Design

## Purpose

This document captures the critical technical design details that the implementation should follow.

It complements [PLAN.md](PLAN.md), which defines the workflow and agent boundaries. This file focuses on concrete design contracts, data shapes, initialization behavior, persistence rules, and the controlled code-execution model.

## Canonical Inputs

The system starts each run from three durable external inputs:

1. The live JWKS Catalog YAML:
   `https://raw.githubusercontent.com/UnitVectorY-Labs/jwks-catalog/refs/heads/main/data/services.yaml`
2. The persisted local `candidates.yaml` from previous runs.
3. The Cloudflare top domains API data for the run.

These inputs serve different roles:

- The live catalog is the official exclusion set.
- `candidates.yaml` is the provisional exclusion set.
- Cloudflare data is the search-space seed.

## Initialization Contract

The initialization stage must run before planning or investigation.

Initialization responsibilities:

1. Create the run record and artifact directories.
2. Download the current `services.yaml`.
3. Parse and normalize catalog entries into SQLite.
4. Load local `candidates.yaml`.
5. Parse and normalize candidate entries into SQLite.
6. Build the current known-issuer exclusion view.
7. Record an initialization summary for the rest of the run.

The rest of the workflow must assume that this has already happened.

## Catalog Import Requirements

The live JWKS Catalog is not schema-perfect, so the importer must normalize field variations instead of assuming one exact YAML key spelling.

At minimum, the importer must tolerate these keys:

- `openid-configuration`
- `openid_configuration`
- `open_id_configuration`
- `jwks_uri`
- `aliases`

The importer should produce a normalized row model such as:

- `source`: `official_catalog`
- `service_id`
- `name`
- `issuer_hint`
- `openid_configuration_url`
- `jwks_uri`
- `aliases`
- `catalog_snapshot_id`

Important rule:

- The catalog import is an exclusion source, not a target for direct automated modification in the first version.

## Candidates Import Requirements

The local `candidates.yaml` represents previously proposed but not yet officially promoted findings.

Those entries must be treated as if they are already known for the purpose of future runs.

That means:

- do not re-propose them as new
- do not re-investigate them unless explicitly marked stale or invalid
- do allow them to be revalidated by targeted maintenance logic in a later version

The importer should store them separately from official catalog entries, but expose a combined known set to planning and review.

Recommended normalized fields:

- `source`: `candidate_file`
- `candidate_id`
- `name`
- `issuer`
- `openid_configuration_url`
- `jwks_uri`
- `aliases`
- `status`
- `candidate_snapshot_id`

## Known Set Semantics

The system should maintain a logical known set composed of:

- all imported official catalog entries
- all imported candidate entries that are still active

This known set should be queryable by:

- issuer
- openid configuration URL
- jwks URI
- primary domain
- alternate domains

This is the core exclusion view used by planning, investigation, and candidate review.

## Persistence Strategy

### SQLite Is For Structured History

SQLite should store:

- run metadata
- normalized catalog and candidate imports
- investigation batches
- summarized domain observations
- probe classifications
- issuer clusters
- candidate decisions
- report indexes

SQLite should not store:

- every raw HTTP response body
- every raw crawl artifact
- arbitrary generated Python code output as unstructured blobs unless specifically retained as an artifact reference

### Files Are For Durable Artifacts

Files should store:

- imported YAML snapshots
- generated `candidates.yaml`
- run reports
- retained positive evidence
- retained ambiguous evidence
- bounded investigation artifacts

Recommended layout:

```text
state/
  oidc-hunter.db
  candidates.yaml
  reports/
  artifacts/
    runs/<run_id>/
      imports/
      investigation/
      probes/
      retained/
```

## Database Design Principles

The database should answer these operational questions efficiently:

- Has this domain already been investigated?
- Has this issuer already been seen?
- Is this candidate already in the official catalog?
- Is this candidate already in `candidates.yaml`?
- Which tactics have historically produced good candidates?
- Which domains or patterns were already exhausted?

Recommended table groups:

- `runs`
- `catalog_snapshots`
- `catalog_entries`
- `candidate_snapshots`
- `candidate_entries`
- `strategy_tactics`
- `run_plans`
- `investigation_batches`
- `domain_state`
- `probe_summary`
- `issuer_cluster`
- `candidate_decision`
- `lessons_learned`

## Domain State Instead of Raw Crawl Exhaust

You explicitly do not want the system to dump every single crawl result into SQLite.

The right compromise is:

- keep a normalized `domain_state` or equivalent table
- record only the latest meaningful classification and selected metadata
- retain richer artifacts only for positives, ambiguous cases, or cases that affected planning

Recommended `domain_state` fields:

- `domain`
- `discovered_by_tactic`
- `first_seen_run_id`
- `last_seen_run_id`
- `last_probe_status`
- `last_probe_classification`
- `last_openid_configuration_url`
- `last_issuer`
- `last_jwks_uri`
- `needs_followup`
- `artifact_ref`

This keeps the database useful without making it a raw crawl log dump.

## Agentic Investigation Model

The most important change from the previous Go implementation is that discovery should not be reduced to a fixed deterministic prefix crawler.

The system should instead use a hybrid model:

1. The planner chooses tactics and budgets.
2. The investigator writes bounded Python code to explore those tactics.
3. Deterministic tools probe and classify the resulting targets.
4. The review loop decides whether findings should be retained.

This is the core reimagining of the crawler as an agentic workflow.

## Bounded Python Execution Model

Only one agent should be allowed to write and execute Python code: the `InvestigatorAgent`.

That code-execution tool should be narrow and opinionated.

It should:

- accept structured task input
- run in a restricted workspace
- expose helper utilities for common operations
- emit structured output
- refuse direct arbitrary persistence into the durable database

Suggested helper capabilities available inside the sandbox:

- read compact plan input
- read compact historical summaries
- emit target domain lists
- emit notes and hypotheses
- call a constrained HTTP helper if needed
- write run-scoped artifacts

Suggested restrictions:

- time limit per execution
- memory limit per execution
- no unrestricted shell access
- no arbitrary write access outside the run artifact directory
- no direct network access beyond configured allowlists if feasible

## Deterministic Verification Model

The actual OIDC verification should remain deterministic.

Recommended flow:

1. Investigator emits candidate targets.
2. Verification tool probes:
   - `https://<domain>/.well-known/openid-configuration`
3. Verification tool classifies result:
   - not found
   - timeout
   - invalid response
   - valid OIDC discovery document
4. For valid responses, normalize:
   - issuer
   - jwks URI
   - host relationship
5. Store only summarized results in SQLite.

This separation matters:

- agentic code performs adaptive search
- deterministic tools perform repeatable verification and persistence

## Candidate Decision Semantics

The candidate review stage should decide among four classes:

- `reject`
- `needs_more_evidence`
- `new_candidate`
- `alternative_domain`

Critical rules:

- If the issuer already exists in the official catalog, reject as known.
- If the issuer already exists in `candidates.yaml`, reject as known.
- If multiple domains point to the same issuer, preserve one canonical candidate and store the others as alternates.
- If the issuer-domain relationship looks suspicious, require more evidence before retaining it.

## `candidates.yaml` Update Contract

The first version's primary durable output is `candidates.yaml`.

That file should be generated deterministically from the database after review is complete.

The update logic should:

1. Load retained provisional candidates from SQLite.
2. Merge them with prior still-active candidate entries.
3. Remove candidates that were explicitly rejected or superseded.
4. Write a canonical ordered YAML file.

Important rule:

- `candidates.yaml` should not be patched incrementally by the LLM.
- It should be rendered from normalized database state by deterministic code.

## Planning Inputs and Search Space

Cloudflare top domains are the main search seed, but they are not the direct answer.

The search problem is:

- start from high-value domains
- infer likely subdomain patterns
- test likely OIDC deployment shapes
- learn from prior outcomes

That means the planner and investigator need access to:

- domain popularity input
- prior tactic performance
- prior invalid patterns
- prior successful domain families
- current known-set exclusions

## Tactic Model

A tactic should be a first-class concept in the database and reporting.

Examples of tactic categories:

- common auth prefixes
- organization-specific naming patterns
- sector-specific conventions
- issuer reuse from related domains
- candidate expansion from previously successful domains

Recommended tactic fields:

- `tactic_id`
- `name`
- `description`
- `input_scope`
- `historical_success_rate`
- `historical_false_positive_rate`
- `last_used_run_id`

This lets the planner improve over time instead of repeating the same exploration blindly.

## Relationship To The Previous Go Implementation

The prior Go implementation established a few useful principles that should be retained:

- SQLite-backed skip logic
- concurrent deterministic probing
- prefix-driven discovery as one tactic
- persistent storage of known results

What should change:

- the system should no longer be a single-purpose batch probe utility
- the search strategy should be dynamic and tactic-driven
- candidate handling should be explicit
- initialization from the live catalog and prior candidates should be mandatory
- the agent should be able to investigate adaptively through bounded Python code

## Implementation Notes

When implementation begins, prioritize in this order:

1. database schema
2. catalog and candidate importers
3. deterministic verification tools
4. candidate export logic
5. bounded investigation code-execution tool
6. ADK workflow wiring

That order matters because the durable operating model is more important than prompt behavior.
