"""ADK wiring for the first-pass planning step."""

from __future__ import annotations

import os

from .config import AppConfig


def _build_model(config: AppConfig):
    if not config.llm_base_url or not config.llm_model:
        return None

    os.environ.setdefault("OPENAI_API_BASE", config.llm_base_url)
    os.environ.setdefault("OPENAI_BASE_URL", config.llm_base_url)
    api_key = config.llm_api_key or "unused"
    os.environ.setdefault("OPENAI_API_KEY", api_key)

    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(
        model=f"openai/{config.llm_model}",
        api_base=config.llm_base_url,
        api_key=api_key,
    )


async def create_run_plan(config: AppConfig, run_id: str, initialization_summary: str) -> str:
    """Use ADK to create one bounded run plan from compact initialization context."""

    model = _build_model(config)
    if model is None:
        return (
            "LLM planning skipped because OIDC_HUNTER_LLM_BASE_URL and "
            "OIDC_HUNTER_LLM_MODEL were not both configured."
        )

    from google.adk.agents import Agent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = Agent(
        name="oidc_hunter_planning_agent",
        model=model,
        instruction=(
            "You are planning a single bounded OIDC discovery run. "
            "Use the supplied initialization summary only. Produce a concise "
            "JSON-like plan with tactics, budgets, stop criteria, and risks. "
            "Do not invent configuration values."
        ),
    )
    app_name = "oidc_hunter"
    user_id = "oidchunter"
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=run_id
    )
    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)
    message = types.Content(
        role="user",
        parts=[
            types.Part(
                text=(
                    "Create the first-pass run plan for this initialization context:\n\n"
                    f"{initialization_summary}"
                )
            )
        ],
    )

    final_text = ""
    try:
        async for event in runner.run_async(
            user_id=user_id, session_id=run_id, new_message=message
        ):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = event.content.parts[0].text or ""
    except Exception as exc:  # ADK/model failures should not corrupt durable state.
        return f"ADK planning failed: {type(exc).__name__}: {exc}"
    return final_text.strip() or "Planning agent returned no final text."
