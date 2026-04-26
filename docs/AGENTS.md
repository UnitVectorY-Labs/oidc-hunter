---
layout: default
title: oidc-hunter
nav_order: 2
permalink: /agents
---

# Agents

The runtime is built as a `SequentialAgent` root workflow with two bounded `LoopAgent`s.

## Root Workflow

1. `InitializeAgent`
   Imports the live JWKS Catalog, imports `data/candidates.yaml`, fetches one Cloudflare Radar seed batch, initializes artifact directories, and seeds tactic metadata.
2. `RunContextAgent`
   Loads recent run summaries, active candidate context, rejection history, tactic scorecards, and coverage summaries from SQLite.
3. `PlanningAgent`
   Uses the configured LiteLLM-backed ADK model to choose a small tactic set and persist the run plan.
4. `InvestigationLoop`
   Runs one tactic at a time, generates targets, probes them, and marks tactic outcomes.
5. `CandidateReviewLoop`
   Reviews issuer clusters that survived probing and either rejects, defers, or promotes them.
6. `CandidatesUpdateAgent`
   Regenerates `data/candidates.yaml` deterministically from SQLite.
7. `ReportingAgent`
   Writes the durable run report, lessons learned, tactic score updates, and closes the run record.

## Investigation Loop

- `InvestigatorAgent`
  Uses the run plan, compact history, and known-set summary to call the bounded Python investigation tool.
- `VerificationAgent`
  Probes the current batch deterministically and persists probe summaries plus issuer clusters.
- `InvestigationTriageAgent`
  Marks tactic completion and exits the loop when the tactic budget is exhausted.

## Candidate Review Loop

- `CandidateAnalysisAgent`
  Loads the next pending issuer cluster and records concise notes about known-set overlap and issuer-domain fit.
- `CandidateDecisionAgent`
  Rejects known findings, marks suspicious results for follow-up, or promotes clusters into the exported candidate set.

## Fallback Behavior

When the configured LLM endpoint is unavailable or the ADK phase exceeds the configured timeout, the application falls back to the deterministic pipeline. That fallback still imports durable state, probes targets, updates `candidates.yaml`, writes a report, and closes the run cleanly.
