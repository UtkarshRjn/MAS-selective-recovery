"""A2ASecBench SUT that records peer provenance and blocks tainted actions."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sut.base import A2AProbeResult, A2ASecuritySUT

from .provenance import Provenance
from .recovery import coarse_taint


class AgentRecA2ASUT(A2ASecuritySUT):
    def __init__(self, max_tasks: int = 5) -> None:
        self.max_tasks = max_tasks

    def run_probe(self, case: dict[str, Any], env: Any | None = None) -> A2AProbeResult:
        if env is None:
            return A2AProbeResult(error_message="Executable A2A environment is required.")
        prov = Provenance()
        if case.get("workflow", {}).get("coordination_type") == "delegated_peer_review":
            return self._delegate(case, env, prov)

        agent = case["agents"][0]
        call = prov.add_step("call_peer", "coordinator")
        response = env.call_agent(agent["agent_id"], case["task"])
        quota_exceeded = len(response.get("task_requests", [])) > self.max_tasks
        unsafe = self._unsafe(response) or quota_exceeded
        peer = prov.add_artifact("peer_response", response, producer=call, faulty=unsafe)
        blocked = 0

        for resource in response.get("resource_refs", []):
            uri = str(resource.get("uri", ""))
            step = prov.add_step("resource", "coordinator")
            prov.add_input(peer, step)
            if step not in coarse_taint(prov):
                result = env.fetch_resource(uri, reason="approved peer resource")
                prov.add_artifact("resource_result", result, producer=step)
            else:
                env.reject_resource(uri, reason="tainted peer resource")
                prov.add_artifact("resource_rejected", {"uri": uri}, producer=step)
                blocked += 1

        for artifact in response.get("artifacts", []):
            step = prov.add_step("artifact", "coordinator")
            prov.add_input(peer, step)
            if step in coarse_taint(prov):
                prov.add_artifact("artifact_rejected", artifact, producer=step)
                blocked += 1
            else:
                result = env.render_artifact(
                    artifact=artifact,
                    execution_sink=artifact.get("execution_sink"),
                    render_mode="active",
                )
                prov.add_artifact("artifact_result", result, producer=step)

        for request in response.get("task_requests", []):
            step = prov.add_step("task_request", "coordinator")
            prov.add_input(peer, step)
            if step not in coarse_taint(prov):
                result = env.create_task(
                    peer_id=agent["agent_id"], campaign_id=request["campaign_id"],
                    task_type=request["task_type"], state=request["state"],
                    description=request["description"],
                )
                prov.add_artifact("task_result", result, producer=step)
            else:
                env.reject_task(
                    peer_id=agent["agent_id"], campaign_id=request["campaign_id"],
                    reason="task quota exceeded" if quota_exceeded else "tainted peer response",
                )
                prov.add_artifact("task_rejected", request, producer=step)
                blocked += 1

        env.final_answer("completed", "Peer workflow processed with provenance checks.")
        return A2AProbeResult(
            response_text="Peer workflow processed.", blocked=blocked > 0,
            metrics={"agentrec_blocked": blocked}, meta={"provenance": prov.to_dict()},
        )

    def _delegate(self, case: dict[str, Any], env: Any, prov: Provenance) -> A2AProbeResult:
        workflow = case["workflow"]
        current = workflow["start_agent_id"]
        source = "coordinator"
        visited: set[str] = set()
        blocked = delegated = 0
        previous: str | None = None
        for _ in range(int(workflow.get("max_peer_hops", 1))):
            visited.add(current)
            step = prov.add_step("delegate", "coordinator", agent_id=current)
            if previous is not None:
                prov.add_input(previous, step)
            response = env.delegate_task(
                source_agent_id=source, target_agent_id=current,
                task_signature=workflow["work_item"], reason="bounded peer review",
            )
            previous = prov.add_artifact("delegation_response", response, producer=step)
            delegated += 1
            next_agent = (response.get("delegation") or {}).get("next_agent_id")
            if response.get("status") == "completed" or not next_agent:
                break
            if str(next_agent) in visited:
                prov.update(previous, faulty=True)
                guard = prov.add_step("delegation_guard", "coordinator")
                prov.add_input(previous, guard)
                if guard in coarse_taint(prov):
                    env.reject_delegation(
                        source_agent_id=current, target_agent_id=str(next_agent),
                        task_signature=workflow["work_item"], reason="cycle detected",
                    )
                    blocked += 1
                    break
            source, current = current, str(next_agent)
        env.final_answer("completed", "Delegation completed within provenance bounds.")
        return A2AProbeResult(
            response_text="Delegation processed.", blocked=blocked > 0,
            metrics={"delegated_count": delegated, "agentrec_blocked": blocked},
            meta={"provenance": prov.to_dict()},
        )

    @staticmethod
    def _unsafe(response: dict[str, Any]) -> bool:
        return any(
            not AgentRecA2ASUT._trusted_resource(str(resource.get("uri", "")))
            for resource in response.get("resource_refs", [])
        ) or any(artifact.get("callback_url") for artifact in response.get("artifacts", []))

    @staticmethod
    def _trusted_resource(uri: str) -> bool:
        parsed = urlparse(uri)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "localhost"}
            and parsed.path.startswith("/approved/")
        )
