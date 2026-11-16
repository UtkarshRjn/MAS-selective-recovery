"""Validate a proposed plan and activate it as a DAG.

Repeated work is a new task node. The monitor rejects a revision that drops a
required goal, cites a missing task, or introduces a cycle.
"""

from __future__ import annotations

from collections import defaultdict, deque

from .contract import Plan, PlanStatus, TaskNode
from .protocol import PlanRevisionProposed


class PlanError(ValueError):
    """The proposed graph is not an acceptable active plan."""


def _ordered_nodes(nodes: dict[str, TaskNode], order: list[str]) -> tuple[TaskNode, ...]:
    return tuple(nodes[task_id] for task_id in order if task_id in nodes)


def assert_dag(task_ids: set[str], edges: tuple[tuple[str, str], ...]) -> None:
    indegree = {task_id: 0 for task_id in task_ids}
    children: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        if src not in indegree or dst not in indegree:
            raise PlanError(f"edge {src} -> {dst} cites a task that is not in the plan")
        children[src].append(dst)
        indegree[dst] += 1
    queue = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
    seen = 0
    while queue:
        task_id = queue.popleft()
        seen += 1
        for child in children[task_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if seen != len(task_ids):
        raise PlanError("plan version must be a DAG")


def goal_coverage(nodes: tuple[TaskNode, ...], required_goals: tuple[str, ...]) -> None:
    covered: set[str] = set()
    for node in nodes:
        covered.update(node.goals)
    missing = [goal for goal in required_goals if goal not in covered]
    if missing:
        raise PlanError(f"required goals are not covered: {', '.join(missing)}")


def apply_revision(current: Plan, proposal: PlanRevisionProposed) -> Plan:
    """Return the next plan version. The caller activates it under the monitor lock."""
    if proposal.base_version != current.version:
        raise PlanError("revision does not cite the active plan version")
    if current.status != PlanStatus.ACTIVE:
        raise PlanError("only the active plan can be revised")
    nodes = {node.task_id: node for node in current.task_nodes}
    order = [node.task_id for node in current.task_nodes]
    for task_id in proposal.remove_tasks:
        if task_id not in nodes:
            raise PlanError(f"cannot remove unknown task {task_id}")
        del nodes[task_id]
    for node in proposal.add_tasks:
        if node.task_id in nodes:
            raise PlanError(f"task {node.task_id} already exists")
        nodes[node.task_id] = node
        order.append(node.task_id)
    edges = set(current.dependency_edges)
    for edge in proposal.remove_edges:
        edges.discard(edge)
    edges.update(proposal.add_edges)
    edges = {edge for edge in edges if edge[0] in nodes and edge[1] in nodes}
    edge_tuple = tuple(sorted(edges))
    assert_dag(set(nodes), edge_tuple)
    goal_coverage(tuple(nodes.values()), proposal.required_goals)
    for task_id in proposal.carry_forward:
        if task_id not in nodes:
            raise PlanError(f"carried-forward task {task_id} is not in the revised plan")
    return Plan(
        version=current.version + 1,
        task_nodes=_ordered_nodes(nodes, order),
        dependency_edges=edge_tuple,
        required_goals=proposal.required_goals,
        status=PlanStatus.ACTIVE,
    )
