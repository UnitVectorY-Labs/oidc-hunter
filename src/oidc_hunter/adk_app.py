"""ADK workflow wiring for oidc-hunter."""

from __future__ import annotations

import inspect
import json
import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from google.adk.agents.base_agent import BaseAgent
from google.genai import types
from pydantic import Field

from .config import AppConfig
from .workflow_tools import WorkflowTools


def _build_model(config: AppConfig):
    if not config.llm_enabled:
        return None

    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(
        model=f"openai/{config.llm_model}",
        api_base=config.llm_base_url,
        api_key=config.llm_api_key or "unused",
        timeout=config.llm_timeout_seconds,
    )


async def run_agentic_workflow(config: AppConfig, run_id: str, run_dir: Path) -> dict[str, Any]:
    """Run the ADK workflow for one oidc-hunter invocation."""

    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    tools = WorkflowTools(config=config, run_id=run_id, run_dir=run_dir)
    model = _build_model(config)
    if model is None:
        return await tools.deterministic_run()

    root_agent = build_root_agent(model=model, tools=tools, config=config)
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="oidc_hunter",
        user_id="oidchunter",
        session_id=run_id,
        state={"run_id": run_id},
    )
    runner = Runner(
        agent=root_agent,
        app_name="oidc_hunter",
        session_service=session_service,
    )
    message = types.Content(
        role="user",
        parts=[types.Part(text="Execute one bounded OIDC discovery run.")],
    )
    async def _consume_runner() -> None:
        async for _ in runner.run_async(
            user_id="oidchunter", session_id=run_id, new_message=message
        ):
            pass

    try:
        await asyncio.wait_for(_consume_runner(), timeout=config.agentic_timeout_seconds)
    except Exception as exc:
        if _should_fallback_to_deterministic(exc):
            tools.local_state["llm_fallback_reason"] = f"{type(exc).__name__}: {exc}"
            return await tools.deterministic_run()
        raise
    return tools.load_run_outcome_summary()


def build_root_agent(model, tools: WorkflowTools, config: AppConfig):
    """Build the ADK root workflow for oidc-hunter."""

    from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
    from google.adk.tools import exit_loop

    initialize_agent = TaskAgent(
        name="InitializeAgent",
        description="Initializes durable state for the run.",
        handler=lambda: tools.initialize_run(),
    )
    run_context_agent = TaskAgent(
        name="RunContextAgent",
        description="Loads prior run context and tactic history.",
        handler=lambda: tools.load_run_context(),
    )
    planning_agent = LlmAgent(
        name="PlanningAgent",
        model=model,
        instruction=(
            "You are planning one bounded OIDC discovery run. "
            "Call `load_cloudflare_batch_metadata`, `sample_candidate_tlds`, and "
            "`load_heuristics_library` to inspect the available search space. "
            "Then call `record_run_plan` exactly once with 2 or 3 tactic ids from the "
            "available library, a short notes string, and concrete success criteria. "
            "Keep the run small and diverse. Do not invent tactic ids."
        ),
        tools=[
            tools.load_cloudflare_batch_metadata,
            tools.sample_candidate_tlds,
            tools.load_heuristics_library,
            tools.record_run_plan,
        ],
    )
    investigator_agent = LlmAgent(
        name="InvestigatorAgent",
        model=model,
        instruction=(
            "You are the bounded investigator. "
            "Call `load_plan_batch` to get the next tactic. If there is no remaining tactic, "
            "do not fabricate work. Otherwise inspect `load_domain_history_summary` and "
            "`load_known_issuers_summary`. Then call `execute_investigation_python` once. "
            "If you provide code, keep it short and use only the provided variables and helper "
            "functions `emit_target(...)` and `record_note(...)`. Do not import modules or "
            "attempt persistence. After targets are generated, call `record_investigation_output`."
        ),
        tools=[
            tools.load_plan_batch,
            tools.load_domain_history_summary,
            tools.load_known_issuers_summary,
            tools.execute_investigation_python,
            tools.record_investigation_output,
        ],
    )
    verification_agent = LlmAgent(
        name="VerificationAgent",
        model=model,
        instruction=(
            "Verify the current investigation batch deterministically. "
            "Call `probe_oidc_candidates` exactly once. Do not invent domains."
        ),
        tools=[tools.probe_oidc_candidates],
    )
    investigation_triage_agent = LlmAgent(
        name="InvestigationTriageAgent",
        model=model,
        instruction=(
            "Call `load_investigation_progress` to inspect the current loop state. "
            "Always call `mark_tactic_outcome` for the tactic that just ran. "
            "If there are no remaining tactics, or the loop has already found enough pending "
            "review clusters, call `exit_loop`. Otherwise stop after marking the tactic outcome."
        ),
        tools=[
            tools.load_investigation_progress,
            tools.mark_tactic_outcome,
            exit_loop,
        ],
    )
    investigation_loop = LoopAgent(
        name="InvestigationLoop",
        description="Investigates Cloudflare seed domains with bounded tactics.",
        sub_agents=[
            investigator_agent,
            verification_agent,
            investigation_triage_agent,
        ],
        max_iterations=config.investigation_iterations,
    )
    candidate_analysis_agent = LlmAgent(
        name="CandidateAnalysisAgent",
        model=model,
        instruction=(
            "Review one candidate cluster at a time. "
            "Call `load_next_candidate_cluster`. If there is no pending cluster, do not invent one. "
            "Otherwise call `load_cluster_evidence` and `record_analysis_notes` with a short summary "
            "covering known-set overlap, issuer-domain relationship, and whether the domains should "
            "be promoted, attached as alternates, rejected, or deferred."
        ),
        tools=[
            tools.load_next_candidate_cluster,
            tools.load_cluster_evidence,
            tools.record_analysis_notes,
        ],
    )
    candidate_decision_agent = LlmAgent(
        name="CandidateDecisionAgent",
        model=model,
        instruction=(
            "Decide the fate of the current cluster. "
            "If there is no current cluster, call `exit_loop`. "
            "Otherwise call `load_cluster_evidence` and then choose exactly one of these actions: "
            "`mark_cluster_rejected`, `mark_cluster_for_followup`, or `promote_cluster`. "
            "Reject anything already known. If the evidence looks suspicious, request follow-up. "
            "If multiple domains resolve to the same new issuer, prefer promotion and allow aliases."
        ),
        tools=[
            tools.load_cluster_evidence,
            tools.mark_cluster_rejected,
            tools.mark_cluster_for_followup,
            tools.promote_cluster,
            exit_loop,
        ],
    )
    review_loop = LoopAgent(
        name="CandidateReviewLoop",
        description="Reviews candidate clusters that survived verification.",
        sub_agents=[candidate_analysis_agent, candidate_decision_agent],
        max_iterations=config.review_iterations,
    )
    candidates_update_agent = TaskAgent(
        name="CandidatesUpdateAgent",
        description="Renders the durable candidates.yaml file.",
        handler=lambda: {
            "candidates": tools.load_candidates_for_export(),
            "write": tools.write_candidates_yaml(),
        },
    )
    reporting_agent = TaskAgent(
        name="ReportingAgent",
        description="Writes durable reports, lessons, and closes the run.",
        handler=lambda: {
            "lessons": tools.append_lessons_learned(),
            "scores": tools.update_strategy_scores(),
            "report": tools.write_run_report(),
            "close": tools.close_current_run(),
        },
    )
    return SequentialAgent(
        name="OidcHunterRootWorkflow",
        description="Sequential OIDC discovery workflow.",
        sub_agents=[
            initialize_agent,
            run_context_agent,
            planning_agent,
            investigation_loop,
            review_loop,
            candidates_update_agent,
            reporting_agent,
        ],
    )


class TaskAgentConfigError(RuntimeError):
    """Raised when a task agent is misconfigured."""


class TaskAgentResultFormatter:
    """Render task outputs into compact event text."""

    @staticmethod
    def render(result: Any) -> str:
        if result is None:
            return "completed"
        if isinstance(result, str):
            return result
        return json.dumps(result, indent=2, sort_keys=True)


class TaskAgent(BaseAgent):  # pragma: no cover - runtime wrapper exercised by integration flows
    """Execute a local callable as one deterministic workflow step."""

    handler: Callable[[], Any] | Callable[[], Awaitable[Any]] = Field(
        exclude=True, repr=False
    )

    async def _run_async_impl(self, ctx):
        from google.adk.events.event import Event

        if not self.handler:
            raise TaskAgentConfigError(f"{self.name} has no handler.")
        result = self.handler()
        if inspect.isawaitable(result):
            result = await result
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(
                role="model",
                parts=[types.Part(text=TaskAgentResultFormatter.render(result))],
            ),
        )

    async def _run_live_impl(self, ctx):
        if False:
            yield ctx


def _should_fallback_to_deterministic(exc: Exception) -> bool:
    name = f"{type(exc).__module__}.{type(exc).__name__}".lower()
    message = str(exc).lower()
    return any(
        marker in name or marker in message
        for marker in (
            "litellm",
            "openai",
            "connection error",
            "connecterror",
            "apiconnectionerror",
            "timeout",
            "timeouterror",
            "dns",
        )
    )
