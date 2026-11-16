"""Independent fault injections, each with its own persistence and detection state.

The first online slices switched every fault off after the first failed check.
That hides a second, independent fault behind the first. Here each fault fires
on its own schedule and is marked detected only when its own claim fails.

``transient``       fires on the first attempt of its task, never again.
``persistent``      fires on every attempt of its task; only a correction
                    from outside that task can replace the value.
``until_detected``  fires until a check fails on its claim.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

PERSISTENCE = ("transient", "persistent", "until_detected")


def logical_task(task_id: str) -> str:
    """`planner:2` is the second visit of `planner`; `planner:2:2` is a revisit of that visit."""
    while True:
        head, _sep, tail = task_id.rpartition(":")
        if not head or not tail.isdigit():
            return task_id
        task_id = head


@dataclass(frozen=True)
class FaultSpec:
    fault_id: str
    task: str
    claim_id: str
    corrupt: Callable[[Any], Any]
    persistence: str = "transient"
    description: str = ""

    def __post_init__(self) -> None:
        if self.persistence not in PERSISTENCE:
            raise ValueError(f"unknown persistence {self.persistence}")


@dataclass
class FaultState:
    fired: list[str] = field(default_factory=list)
    detected: bool = False


class FaultSchedule:
    def __init__(self, specs: tuple[FaultSpec, ...] | list[FaultSpec] = ()) -> None:
        self.specs = {spec.fault_id: spec for spec in specs}
        self.state = {spec.fault_id: FaultState() for spec in specs}

    def applies(self, spec: FaultSpec, task_id: str) -> bool:
        return logical_task(task_id) == spec.task

    def should_fire(self, spec: FaultSpec) -> bool:
        state = self.state[spec.fault_id]
        if spec.persistence == "transient":
            return not state.fired
        if spec.persistence == "until_detected":
            return not state.detected
        return True

    def apply(self, task_id: str, attempt_id: str, outputs: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Corrupt the outputs at the source output boundary. Returns the fired fault ids."""
        fired: list[str] = []
        result = dict(outputs)
        for spec in self.specs.values():
            if not self.applies(spec, task_id) or spec.claim_id not in result:
                continue
            if not self.should_fire(spec):
                continue
            result[spec.claim_id] = spec.corrupt(copy.deepcopy(result[spec.claim_id]))
            self.state[spec.fault_id].fired.append(attempt_id)
            fired.append(spec.fault_id)
        return result, fired

    def detected(self, claim_ids: set[str] | list[str] | tuple[str, ...]) -> list[str]:
        marked: list[str] = []
        for spec in self.specs.values():
            if spec.claim_id in claim_ids and not self.state[spec.fault_id].detected:
                self.state[spec.fault_id].detected = True
                marked.append(spec.fault_id)
        return marked

    def fired_attempts(self) -> dict[str, list[str]]:
        return {fault_id: list(state.fired) for fault_id, state in self.state.items()}

    def by_claim(self, claim_id: str) -> list[FaultSpec]:
        return [spec for spec in self.specs.values() if spec.claim_id == claim_id]
