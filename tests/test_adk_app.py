import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oidc_hunter.adk_app import run_agentic_workflow
from oidc_hunter.config import AppConfig


class _FakeSessionService:
    async def create_session(self, **kwargs):
        return kwargs


class _FakeWorkflowTools:
    instances = []

    def __init__(self, config, run_id, run_dir):
        self.config = config
        self.run_id = run_id
        self.run_dir = run_dir
        self.local_state = {}
        self.logs = []
        self.deterministic_plan_calls = 0
        self.deterministic_investigation_calls = 0
        self.deterministic_review_calls = 0
        self.finalize_calls = 0
        self.fallbacks = []
        type(self).instances.append(self)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def initialize_run(self):
        self.local_state["initialized"] = True

    def load_run_context(self):
        self.local_state["context_loaded"] = True

    def deterministic_plan(self):
        self.deterministic_plan_calls += 1
        self.local_state["run_plan"] = {"selected_tactics": ["fallback"]}

    async def deterministic_investigation(self):
        self.deterministic_investigation_calls += 1

    def deterministic_review(self):
        self.deterministic_review_calls += 1

    def finalize_run(self):
        self.finalize_calls += 1
        return {"run_id": self.run_id, "status": "completed", "summary": "ok"}

    def record_fallback(self, stage_name: str, reason: str):
        self.fallbacks.append({"stage": stage_name, "reason": reason})
        self.local_state["llm_fallback_reasons"] = list(self.fallbacks)
        self.local_state["llm_fallback_reason"] = f"{stage_name}: {reason}"

    def has_recorded_run_plan(self) -> bool:
        return "run_plan" in self.local_state

    def has_investigation_activity(self) -> bool:
        return "completed_tactics" in self.local_state

    def has_review_activity(self) -> bool:
        return "reviewed_clusters" in self.local_state


class AgenticWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _FakeWorkflowTools.instances.clear()

    async def test_agentic_stages_complete_without_deterministic_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                state_dir=Path(tmp) / "state",
                llm_base_url="http://example.invalid/v1",
                llm_model="fake-model",
                llm_api_key="unused",
            )
            config.ensure_directories()
            run_dir = config.artifacts_dir / "runs" / "run-agentic"
            run_dir.mkdir(parents=True, exist_ok=True)

            async def fake_run_stage_agent(**kwargs):
                tools = _FakeWorkflowTools.instances[-1]
                prompt = kwargs["prompt"]
                lowered = prompt.lower()
                if "investigation" in lowered:
                    tools.local_state["completed_tactics"] = ["common_auth_prefixes"]
                elif "review" in lowered:
                    tools.local_state["reviewed_clusters"] = ["cluster-1"]
                elif "plan" in lowered:
                    tools.local_state["run_plan"] = {"selected_tactics": ["agentic"]}

            with patch("oidc_hunter.adk_app.WorkflowTools", _FakeWorkflowTools), patch(
                "oidc_hunter.adk_app._build_model", return_value=object()
            ), patch(
                "oidc_hunter.adk_app.build_stage_agents",
                return_value={"planning": object(), "investigation": object(), "review": object()},
            ), patch(
                "oidc_hunter.adk_app._run_stage_agent",
                side_effect=fake_run_stage_agent,
            ), patch(
                "google.adk.sessions.InMemorySessionService",
                _FakeSessionService,
            ):
                outcome = await run_agentic_workflow(
                    config=config,
                    run_id="run-agentic",
                    run_dir=run_dir,
                )

            tools = _FakeWorkflowTools.instances[-1]
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(tools.deterministic_plan_calls, 0)
            self.assertEqual(tools.deterministic_investigation_calls, 0)
            self.assertEqual(tools.deterministic_review_calls, 0)
            self.assertEqual(tools.finalize_calls, 1)
            self.assertEqual(tools.fallbacks, [])
            self.assertIn("run_plan", tools.local_state)

    async def test_agentic_stage_timeouts_fallback_to_deterministic_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(
                state_dir=Path(tmp) / "state",
                llm_base_url="http://example.invalid/v1",
                llm_model="fake-model",
                llm_api_key="unused",
            )
            config.ensure_directories()
            run_dir = config.artifacts_dir / "runs" / "run-fallback"
            run_dir.mkdir(parents=True, exist_ok=True)

            async def timeout_stage_agent(**kwargs):
                raise TimeoutError("simulated timeout")

            with patch("oidc_hunter.adk_app.WorkflowTools", _FakeWorkflowTools), patch(
                "oidc_hunter.adk_app._build_model", return_value=object()
            ), patch(
                "oidc_hunter.adk_app.build_stage_agents",
                return_value={"planning": object(), "investigation": object(), "review": object()},
            ), patch(
                "oidc_hunter.adk_app._run_stage_agent",
                side_effect=timeout_stage_agent,
            ), patch(
                "google.adk.sessions.InMemorySessionService",
                _FakeSessionService,
            ):
                outcome = await run_agentic_workflow(
                    config=config,
                    run_id="run-fallback",
                    run_dir=run_dir,
                )

            tools = _FakeWorkflowTools.instances[-1]
            self.assertEqual(outcome["status"], "completed")
            self.assertEqual(tools.deterministic_plan_calls, 1)
            self.assertEqual(tools.deterministic_investigation_calls, 1)
            self.assertEqual(tools.deterministic_review_calls, 1)
            self.assertEqual(tools.finalize_calls, 1)
            self.assertEqual(
                [fallback["stage"] for fallback in tools.fallbacks],
                ["planning", "investigation", "review"],
            )


if __name__ == "__main__":
    unittest.main()
