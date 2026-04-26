# OIDC Finder ADK Plan

## Purpose

Build a daily-run autonomous agent in Python using Google's Agent Development Kit (ADK) that discovers new public OIDC issuers, avoids repeating known work, and updates `candidates.yaml` with new reviewable findings for later manual promotion into the JWKS Catalog.

This document defines the workflow, agent boundaries, and operating model. The more concrete technical details live in [DESIGN.md](DESIGN.md).

## Primary Goal

On each run, the agent should:

1. Initialize its working state from the live JWKS Catalog and the persisted `candidates.yaml`.
2. Review prior crawl history and lessons learned.
3. Produce a fresh, bounded strategy for what to explore next.
4. Perform agent-driven investigation with deterministic verification and persistence tools.
5. Analyze valid findings and deduplicate by issuer and domain relationship.
6. Update `candidates.yaml`.
7. Write a durable run report and update durable knowledge for the next run.

## High-Level Objective

The intended operator experience is:

1. You kick off the process.
2. The main external input is the Cloudflare top domains API.
3. A novel plan is constructed for what to look for next.
4. The actual search and verification are performed by the agent with the aid of deterministic tools.
5. `candidates.yaml` is updated for human review and future runs.

## Non-Goals For Initial Version

- No autonomous updates to the official `jwks-catalog` repository.
- No autonomous merge to the main branch of the catalog repository.
- No dependence on ADK's long-term memory features for durable business state.
- No requirement for remote databases or multi-user sessions.
- No assumption that the LLM should directly process large domain lists, raw HTTP responses, or large crawl outputs.

## Design Constraints

- Runs once, then exits.
- Must work in a local Docker container.
- Durable state must live in:
  - one local SQLite database
  - text or markdown files stored beside that database
  - these files are mounted as volumes in the container and persist across runs
- Uses a local model by default.
  - the OpenAI-compatible Chat Completions endpoint URL, model name, and optional credentials are supplied as configuration
- Workflow runs fully autonomously with no human input after startup.
- The crawl strategy should adapt over time instead of replaying the exact same static scan.
- The official JWKS Catalog must be treated as a live exclusion source for each run.
- Existing `candidates.yaml` entries from prior runs must be treated as already-known findings.

## Key ADK Design Decisions

### 1. Use stable workflow agents, not ADK 2.0 graphs, for v1

ADK's graph-based workflows are currently documented as Python `v2.0.0 Beta`. The first version should stay on stable `SequentialAgent` and `LoopAgent` patterns.

Upgrade path:

- Start with stable workflow agents.
- Keep tool interfaces clean and structured.
- Revisit graph workflows later if more explicit routing becomes necessary.

### 2. Treat ADK session state as per-run scratch space only

The durable system of record should be SQLite plus persisted files. ADK session state is useful for handoff between agents during one run, but not for the product's durable operating history.

The SQLite database is not agent memory. It is the operational history of what has already been explored, what was found, what was rejected, and what remains a candidate.

Recommended approach:

- Use `InMemorySessionService` for a single run.
- Use a run-scoped session such as `user_id=oidchunter`, `session_id=<run_id>`.
- Persist all durable knowledge through custom tools into SQLite and files.

### 3. Add an explicit initialization stage

The run must begin by reconciling durable inputs into SQLite:

- the live JWKS Catalog `services.yaml`
- the persisted `candidates.yaml`
- local run metadata and prior crawl history

This is not optional bookkeeping. It establishes the exclusion set that the rest of the run depends on.

### 4. Keep hard work in code, but allow bounded agent-authored investigation code

The design should not collapse into a purely deterministic batch crawler. The discovery portion should be more adaptive than the previous Go implementation.

Recommended approach:

- Deterministic tools remain responsible for:
  - data import
  - HTTP probing
  - SQLite persistence
  - artifact management
  - final `candidates.yaml` generation
- A dedicated investigation agent is allowed to write and execute bounded Python code in a sandboxed environment for exploration and hypothesis testing.
- That investigation code must not become the primary persistence path. Structured writes still happen through tools.

This preserves the key requirement that hard computation runs in code, not in the prompt, while still allowing agentic adaptation.

### 5. Use LLM agents for bounded reasoning and controlled experimentation

The model should:

- choose strategy
- rank hypotheses
- decide what investigation tactics are worth trying
- write bounded Python investigation code
- interpret summarized evidence
- decide whether a candidate should be retained, rejected, or merged with an existing issuer
- write reports and lessons learned

The model should not:

- ingest raw Cloudflare ranking dumps directly
- parse large raw crawl logs directly
- write arbitrary files anywhere in the container
- write directly to SQLite except through controlled tool surfaces

### 6. Prefer sequential orchestration and bounded loops

Because the plan is to use a local model, inference concurrency should be conservative.

Recommended rule:

- `SequentialAgent` is the main orchestration primitive.
- `LoopAgent` is used for investigation and candidate review.
- `ParallelAgent` is deferred unless a concrete need appears later.

## High-Level Workflow

The root workflow should be a `SequentialAgent` with these stages:

1. `InitializeAgent`
2. `RunContextAgent`
3. `PlanningAgent`
4. `InvestigationLoop`
5. `CandidateReviewLoop`
6. `CandidatesUpdateAgent`
7. `ReportingAgent`

Each stage should write small, structured outputs into ADK state, while large artifacts stay on disk or in SQLite.

## Recommended Agent Breakdown

### 1. InitializeAgent

Purpose:

- Build the current exclusion and context baseline for the run.

Responsibilities:

- download the latest live JWKS Catalog `services.yaml`
- import catalog entries into SQLite
- load persisted `candidates.yaml`
- import those candidates into SQLite as provisional known findings
- initialize the run record and artifact directories

Tools:

- `start_run(config_ref)`
- `fetch_live_catalog(url)`
- `import_catalog_yaml(artifact_ref)`
- `load_candidates_yaml(path)`
- `import_candidates_yaml(path_or_ref)`
- `initialize_artifact_layout(run_id)`
- `finalize_initialization(run_id)`

Output state:

- `initialization_summary`
- `catalog_snapshot_summary`
- `candidate_snapshot_summary`

### 2. RunContextAgent

Purpose:

- Load a compact operational briefing for this run.

Input:

- run configuration
- recent run summaries
- unresolved candidate backlog
- recent rejected candidates
- coverage and exploration summaries

Tools:

- `load_run_context(run_id, limits...)`
- `load_recent_reports(days, max_reports)`
- `load_strategy_backlog(limit)`
- `load_exploration_coverage_summary()`

Output state:

- `run_brief`
- `backlog_summary`
- `coverage_summary`

### 3. PlanningAgent

Purpose:

- Produce the plan for this run.

Responsibilities:

- identify underexplored areas
- decide which discovery tactics to try
- set budgets and stop criteria
- select bounded domain batches

Tools:

- `load_cloudflare_batch_metadata()`
- `sample_candidate_tlds(limit, filters...)`
- `load_heuristics_library()`
- `record_run_plan(plan_json)`

Output state:

- `run_plan`
- `selected_tactics`
- `success_criteria`

### 4. InvestigationLoop

This should be a `LoopAgent`.

Sub-agents:

1. `InvestigatorAgent`
2. `VerificationAgent`
3. `InvestigationTriageAgent`

Purpose:

- Let the system adaptively investigate domains and candidate subdomains without turning the whole pipeline into prompt-only reasoning.

#### 4a. InvestigatorAgent

Purpose:

- Use the run plan to explore promising patterns and generate candidate targets.

Special capability:

- This is the one agent that may write and execute bounded Python investigation code.

Responsibilities:

- choose tactics from the plan
- inspect compact historical summaries
- write short Python programs that propose domains, enumerate paths, or cluster patterns
- emit structured candidate target lists and investigation notes

Tools:

- `load_plan_batch(plan_ref)`
- `load_domain_history_summary(scope_ref)`
- `load_known_issuers_summary()`
- `execute_investigation_python(spec_or_code_ref)`
- `record_investigation_output(run_id, artifact_ref, summary)`

Output state:

- `investigation_summary`
- `proposed_target_batch_refs`

#### 4b. VerificationAgent

Purpose:

- Perform deterministic probing and store summarized outcomes.

Responsibilities:

- verify candidate targets from the investigator
- classify probe outcomes
- persist only summarized crawl results and positive evidence

Tools:

- `probe_oidc_candidates(input_ref, concurrency, timeout)`
- `classify_probe_results(results_ref)`
- `store_probe_summary(run_id, summary_ref)`
- `store_positive_evidence(run_id, evidence_ref)`

Output state:

- `probe_summary`
- `candidate_cluster_refs`

#### 4c. InvestigationTriageAgent

Purpose:

- Decide whether the loop should continue with more tactics or stop.

Tools:

- `load_investigation_progress(run_id)`
- `mark_tactic_outcome(run_id, tactic_id, outcome)`
- `exit_investigation_loop()`

Output state:

- `investigation_progress`

Loop mechanics:

- Each iteration runs one bounded investigation step.
- The loop exits when:
  - the run plan budget is exhausted
  - no promising next tactic remains
  - or the exit tool is called

### 5. CandidateReviewLoop

This should be a `LoopAgent`.

Sub-agents:

1. `CandidateAnalysisAgent`
2. `CandidateDecisionAgent`

Purpose:

- Review candidate clusters that survived deterministic verification.

#### 5a. CandidateAnalysisAgent

Purpose:

- Review summarized evidence for a candidate cluster.
- detect issuer/domain mismatches
- detect multiple domains pointing at the same issuer
- request more probing when required

Tools:

- `load_next_candidate_cluster(run_id)`
- `load_cluster_evidence(cluster_id)`
- `request_followup_probe(cluster_id, probe_type)`
- `record_analysis_notes(cluster_id, notes)`

Output state:

- `current_cluster_review`
- `followup_actions`

#### 5b. CandidateDecisionAgent

Purpose:

- Make the bounded decision for the current cluster.

Decision classes:

- `reject`
- `needs_more_evidence`
- `promote_as_new_candidate`
- `attach_as_alternative_domain`

Tools:

- `mark_cluster_rejected(cluster_id, reason)`
- `mark_cluster_for_followup(cluster_id, actions)`
- `promote_cluster(cluster_id, promotion_type, canonical_issuer)`
- `exit_review_loop()`

Output state:

- `review_decision`

Critical review rules:

- If the finding already exists in the live catalog, reject it as already known.
- If it already exists in `candidates.yaml`, reject it as already known.
- If multiple discovered domains resolve to the same issuer, only one canonical issuer record is retained.
- Additional domains become alternates linked to the canonical issuer.

### 6. CandidatesUpdateAgent

Purpose:

- Regenerate `candidates.yaml` from the database state after the run.

Responsibilities:

- assemble approved provisional candidates
- merge retained prior candidates that are still unresolved
- write a deterministic `candidates.yaml`
- produce a summary of additions, removals, and unchanged entries

Tools:

- `load_candidates_for_export(run_id)`
- `render_candidates_yaml(run_id)`
- `write_candidates_yaml(path, artifact_ref)`
- `record_candidates_update(run_id, summary)`

Output state:

- `candidates_update_summary`

### 7. ReportingAgent

Purpose:

- Write durable lessons and run output.

Responsibilities:

- summarize what worked
- summarize what failed
- record new heuristics
- capture rejected tactics
- close the run

Tools:

- `load_run_outcome_summary(run_id)`
- `write_run_report(run_id, markdown)`
- `append_lessons_learned(entries)`
- `update_strategy_scores(run_id, scorecard)`
- `close_run(run_id, status)`

Output state:

- `run_report`
- `lessons_summary`

## Why This Workflow Fits ADK Well

### Sequential structure where order is fixed

The run has a strict order:

1. initialize
2. build context
3. make plan
4. investigate
5. review candidates
6. update `candidates.yaml`
7. report

That is what `SequentialAgent` is for.

### Loops only where iteration is genuinely needed

Two phases benefit from iteration:

- investigation
- candidate review

That is what `LoopAgent` is for.

### Tools do the durable and high-volume work

ADK function tools are the right abstraction for:

- SQLite access
- YAML import and export
- HTTP probing
- artifact management
- classification persistence
- reporting

This keeps large data and hard work out of prompts while still leaving room for adaptive code-based investigation.

## Persistent State Model

### SQLite

SQLite should be the durable operational store.

Recommended table groups:

- `runs`
- `catalog_snapshots`
- `catalog_entries`
- `candidate_snapshots`
- `candidate_entries`
- `run_plans`
- `strategy_tactics`
- `domain_batches`
- `investigation_batches`
- `domain_observations`
- `probe_summaries`
- `positive_evidence`
- `issuer_clusters`
- `candidate_decisions`
- `rejections`
- `lessons_learned`

Rules:

- Do not store every raw crawl response in SQLite.
- Store summaries, classifications, canonical identifiers, and references to artifacts.
- Positive evidence and ambiguous cases may retain richer artifacts on disk.

### Files Beside the Database

Recommended durable file layout:

```text
state/
  oidc-hunter.db
  candidates.yaml
  reports/
    2026-04-26-run-001.md
  lessons/
    LESSONS_LEARNED.md
    TACTICS.md
  artifacts/
    runs/<run_id>/
      imports/
      planning/
      investigation/
      probes/
      summaries/
```

## Context Window Management

Rules:

- Never place full Cloudflare domain lists in prompt context.
- Never place raw probe output files in prompt context.
- Never place large HTML or JSON bodies in prompt context unless summarized.
- Tools should return:
  - counts
  - top samples
  - histograms
  - compact summaries
  - artifact references

Pattern:

1. Tool executes on the full dataset.
2. Tool stores full result to disk and SQLite.
3. Tool returns only a compact summary and stable references.
4. Agent reasons over that summary.

## Skills Strategy

ADK Skills are useful here as prompt assets, not as the primary persistence layer.

Recommended skill directories:

- `skills/oidc_hunting/`
- `skills/cloudflare_radar/`
- `skills/catalog_policy/`

## Tooling Strategy

All important tools should be custom Python function tools with explicit typed signatures and structured outputs.

Recommended tool categories:

### Initialization tools

- fetch live catalog
- parse and normalize catalog YAML
- load and normalize `candidates.yaml`
- seed current run state

### Database tools

- query summaries
- insert and update records
- fetch next work item

### Investigation tools

- execute bounded Python investigation code
- record proposed targets
- summarize prior domain history

### Verification tools

- probe `.well-known/openid-configuration`
- normalize issuer and jwks metadata
- classify results
- persist summarized outcomes

### Export tools

- render `candidates.yaml`
- write reports

## Command Execution and Sandboxing

The plan should allow a bounded code-writing investigator without giving the whole system arbitrary shell access.

Recommended safety model:

- Only the `InvestigatorAgent` gets code execution capability.
- That capability should be exposed as a narrow tool such as `execute_investigation_python(...)`.
- Generated code should run in a restricted workspace with:
  - a time limit
  - a memory limit
  - a controlled set of helper libraries
  - access only to run-specific artifact directories
- Durable writes to SQLite should happen through separate controlled tools.

Practical implication:

- Prefer `execute_investigation_python(plan_ref)` plus `record_investigation_output(...)`
- Avoid a generic `run_any_command(...)` interface

## Repo Layout Recommendation

```text
oidc-finder-v2/
  README.md
  PLAN.md
  DESIGN.md
  pyproject.toml
  src/oidc_hunter/
    agent.py
    config.py
    workflows/
      root.py
      investigation_loop.py
      review_loop.py
    agents/
      initialize.py
      run_context.py
      planning.py
      investigator.py
      verification.py
      investigation_triage.py
      candidate_analysis.py
      candidate_decision.py
      candidates_update.py
      reporting.py
    tools/
      init.py
      db.py
      cloudflare.py
      investigation.py
      probing.py
      clustering.py
      candidates.py
      reporting.py
      safety.py
    storage/
      sqlite.py
      artifacts.py
    schemas/
  skills/
    oidc_hunting/
    cloudflare_radar/
    catalog_policy/
  scripts/
    run_daily.py
  Dockerfile
  tests/
```

## Recommended First-Run Behavior

The first run should:

- import the live catalog
- import the existing `candidates.yaml`
- use a small bounded Cloudflare batch
- try a small number of tactics
- fully exercise the end-to-end workflow
- update `candidates.yaml`
- produce a run report even if no new candidate survives review

## Final Recommendation

The first version should be a hybrid ADK workflow:

- `SequentialAgent` for the full daily pipeline
- one bounded `LoopAgent` for investigation
- one bounded `LoopAgent` for candidate review
- deterministic tools for import, verification, persistence, and export
- one narrowly scoped sandboxed Python execution tool for the investigation agent
- SQLite plus persisted files as the durable operating memory

That design stays faithful to the agentic goal without losing the operational discipline that the crawler requires.

## References

- ADK overview: https://adk.dev/get-started/about/
- Sequential agents: https://adk.dev/agents/workflow-agents/sequential-agents/
- Loop agents: https://adk.dev/agents/workflow-agents/loop-agents/
- Function tools: https://adk.dev/tools-custom/function-tools/
- Sessions: https://adk.dev/sessions/session/
- State: https://adk.dev/sessions/state/
- Skills: https://adk.dev/skills/
- Tool limitations: https://adk.dev/tools/limitations/
- Graph workflows: https://adk.dev/workflows/
- Live JWKS Catalog YAML: https://raw.githubusercontent.com/UnitVectorY-Labs/jwks-catalog/refs/heads/main/data/services.yaml
- Previous deterministic implementation: https://github.com/UnitVectorY-Labs/oidcfinder
