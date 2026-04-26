"""ADK workflow wiring for oidc-hunter."""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import time
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

    from google.adk.sessions import InMemorySessionService

    tools = WorkflowTools(config=config, run_id=run_id, run_dir=run_dir)
    model = _build_model(config)

    tools.log("Initializing run state.")
    tools.initialize_run()
    tools.log("Loading durable run context.")
    tools.load_run_context()

    if model is None:
        tools.log("LLM not configured; running deterministic workflow.")
        tools.deterministic_plan()
        await tools.deterministic_investigation()
        tools.deterministic_review()
        return tools.finalize_run()

    stage_agents = build_stage_agents(model=model, tools=tools, config=config)
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name="oidc_hunter",
        user_id="oidchunter",
        session_id=run_id,
        state={"run_id": run_id},
    )

    started_at = time.monotonic()
    await _run_stage_with_fallback(
        stage_name="planning",
        agent=stage_agents["planning"],
        prompt="Produce one bounded OIDC discovery plan for this run.",
        timeout_seconds=_remaining_stage_budget(
            started_at=started_at,
            total_budget=config.agentic_timeout_seconds,
            desired_budget=max(20.0, min(45.0, config.agentic_timeout_seconds * 0.25)),
        ),
        run_id=run_id,
        session_service=session_service,
        tools=tools,
        fallback=tools.deterministic_plan,
        completion_check=tools.has_recorded_run_plan,
    )
    await _run_stage_with_fallback(
        stage_name="investigation",
        agent=stage_agents["investigation"],
        prompt="Execute the bounded OIDC investigation loop for the current run plan.",
        timeout_seconds=_remaining_stage_budget(
            started_at=started_at,
            total_budget=config.agentic_timeout_seconds,
            desired_budget=max(45.0, min(90.0, config.agentic_timeout_seconds * 0.5)),
        ),
        run_id=run_id,
        session_service=session_service,
        tools=tools,
        fallback=tools.deterministic_investigation,
        completion_check=tools.has_investigation_activity,
    )
    await _run_stage_with_fallback(
        stage_name="review",
        agent=stage_agents["review"],
        prompt="Review any pending OIDC candidate clusters and decide their outcomes.",
        timeout_seconds=_remaining_stage_budget(
            started_at=started_at,
            total_budget=config.agentic_timeout_seconds,
            desired_budget=max(20.0, min(45.0, config.agentic_timeout_seconds * 0.25)),
        ),
        run_id=run_id,
        session_service=session_service,
        tools=tools,
        fallback=tools.deterministic_review,
        completion_check=tools.has_review_activity,
    )
    return tools.finalize_run()


def build_stage_agents(model, tools: WorkflowTools, config: AppConfig) -> dict[str, BaseAgent]:
    """Build the ADK agents used for the bounded workflow stages."""

    from google.adk.agents import LlmAgent, LoopAgent
    from google.adk.tools import exit_loop

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
    investigation_iteration_agent = LlmAgent(
        name="InvestigationAgent",
        model=model,
        instruction=(
            "You are running one bounded investigation iteration. "
            "First call `load_plan_batch`. If there is no remaining tactic, call `exit_loop`. "
            "Otherwise inspect `load_domain_history_summary` and `load_known_issuers_summary`, "
            "then call `execute_investigation_python` exactly once. If you provide code, keep it "
            "short and use only the provided variables plus `emit_target(...)` and "
            "`record_note(...)`; do not import modules or attempt persistence. "
            "After target generation, call `record_investigation_output`, then call "
            "`probe_oidc_candidates`, then call `load_investigation_progress`, then always call "
            "`mark_tactic_outcome` for the tactic that just ran. If there are no remaining tactics "
            "after that, or there are already enough pending review clusters, call `exit_loop`."
        ),
        tools=[
            tools.load_plan_batch,
            tools.load_domain_history_summary,
            tools.load_known_issuers_summary,
            tools.execute_investigation_python,
            tools.record_investigation_output,
            tools.probe_oidc_candidates,
            tools.load_investigation_progress,
            tools.mark_tactic_outcome,
            exit_loop,
        ],
    )
    investigation_loop = LoopAgent(
        name="InvestigationLoop",
        description="Investigates Cloudflare seed domains with bounded tactics.",
        sub_agents=[investigation_iteration_agent],
        max_iterations=config.investigation_iterations,
    )
    candidate_review_agent = LlmAgent(
        name="CandidateReviewAgent",
        model=model,
        instruction=(
            "Review one pending cluster at a time. "
            "Call `load_next_candidate_cluster`. If there is no pending cluster, call `exit_loop`. "
            "Otherwise call `load_cluster_evidence`, then `record_analysis_notes` with a short "
            "summary covering known-set overlap, issuer-domain relationship, and whether the "
            "domains should be promoted, attached as alternates, rejected, or deferred. Then choose "
            "exactly one of these actions: `mark_cluster_rejected`, `mark_cluster_for_followup`, "
            "or `promote_cluster`. Reject anything already known. If the evidence looks suspicious, "
            "request follow-up. If multiple domains resolve to the same new issuer, prefer promotion "
            "and allow aliases."
        ),
        tools=[
            tools.load_next_candidate_cluster,
            tools.load_cluster_evidence,
            tools.record_analysis_notes,
            tools.mark_cluster_rejected,
            tools.mark_cluster_for_followup,
            tools.promote_cluster,
            exit_loop,
        ],
    )
    review_loop = LoopAgent(
        name="CandidateReviewLoop",
        description="Reviews candidate clusters that survived verification.",
        sub_agents=[candidate_review_agent],
        max_iterations=config.review_iterations,
    )
    return {
        "planning": planning_agent,
        "investigation": investigation_loop,
        "review": review_loop,
    }


def build_root_agent(model, tools: WorkflowTools, config: AppConfig):
    """Build the ADK root workflow for oidc-hunter."""

    from google.adk.agents import SequentialAgent

    stage_agents = build_stage_agents(model=model, tools=tools, config=config)
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
            stage_agents["planning"],
            stage_agents["investigation"],
            stage_agents["review"],
            candidates_update_agent,
            reporting_agent,
        ],
    )


def _remaining_stage_budget(
    *, started_at: float, total_budget: float, desired_budget: float
) -> float:
    elapsed = time.monotonic() - started_at
    remaining = max(5.0, total_budget - elapsed)
    return max(5.0, min(desired_budget, remaining))


async def _run_stage_with_fallback(
    *,
    stage_name: str,
    agent: BaseAgent,
    prompt: str,
    timeout_seconds: float,
    run_id: str,
    session_service,
    tools: WorkflowTools,
    fallback: Callable[[], Any] | Callable[[], Awaitable[Any]],
    completion_check: Callable[[], bool] | None = None,
) -> None:
    tools.log(f"Starting {stage_name} stage with ADK (timeout={timeout_seconds:.0f}s).")
    try:
        await _run_stage_agent(
            agent=agent,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            run_id=run_id,
            session_service=session_service,
        )
        if completion_check is not None and not completion_check():
            raise TimeoutError(f"{stage_name} stage completed without producing durable state.")
        tools.log(f"Completed {stage_name} stage with ADK.")
        return
    except Exception as exc:
        if not _should_fallback_to_deterministic(exc):
            raise
        reason = f"{type(exc).__name__}: {exc}"
        if completion_check is not None and completion_check():
            tools.log(
                f"{stage_name.capitalize()} stage reached durable state before timeout; "
                "continuing without deterministic replay."
            )
            tools.record_fallback(stage_name, f"nonfatal_timeout_after_completion: {reason}")
            return
        tools.record_fallback(stage_name, reason)
        tools.log(f"Falling back to deterministic {stage_name} stage: {reason}")
        result = fallback()
        if inspect.isawaitable(result):
            await result


async def _run_stage_agent(
    *,
    agent: BaseAgent,
    prompt: str,
    timeout_seconds: float,
    run_id: str,
    session_service,
) -> None:
    from google.adk.runners import Runner

    runner = Runner(
        agent=agent,
        app_name="oidc_hunter",
        session_service=session_service,
    )
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    async def _consume_runner() -> None:
        async for _ in runner.run_async(
            user_id="oidchunter", session_id=run_id, new_message=message
        ):
            pass

    await asyncio.wait_for(_consume_runner(), timeout=timeout_seconds)


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
