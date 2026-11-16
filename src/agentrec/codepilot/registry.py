"""Frozen AsynCodeBench release registry.

Loads the v0.4 release from a checkout pinned to one commit and verifies the
sha256 of every artifact the task index lists (task, scenario, metric, quality,
annotation records), the overlays each task applies, the execution profile,
and the validated-baseline registry. Any mismatch raises: official runs must
use the frozen registry, not files that drifted after the release.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PINNED_COMMIT = "2a639b049f249e79eb2031876bc06a110be9ac3a"
RELEASE = "v0.4"


class RegistryError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Assignment:
    agent_id: str
    role: str
    subproblem: str
    writable_paths: tuple[str, ...]
    primary_tests: tuple[str, ...]


@dataclass(frozen=True)
class DependencyPoint:
    dependency_id: str
    dependency_type: str
    producer_agent: str
    consumer_agent: str
    producer_files: tuple[str, ...]
    consumer_files: tuple[str, ...]
    upstream_probes: tuple[str, ...]
    downstream_probes: tuple[str, ...]
    integrated_probes: tuple[str, ...]
    contract: str

    @property
    def executable(self) -> bool:
        """Both sides have probes and the producer and consumer are different agents."""
        return bool(self.upstream_probes and self.downstream_probes) and self.producer_agent != self.consumer_agent


@dataclass(frozen=True)
class Overlay:
    path: str
    sha256: str


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    repository: str
    benchmark: str
    origin_url: str
    base_sha: str
    overlays: tuple[Overlay, ...]
    evaluator_command: tuple[str, ...]
    test_targets: tuple[str, ...]
    problem_statement: str
    official_result_eligible: bool
    assignments: tuple[Assignment, ...]
    dependency_points: tuple[DependencyPoint, ...]
    budgets: dict[str, Any]
    image: str = ""
    image_digest: str = ""
    artifacts: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def short_name(self) -> str:
        return self.task_id.split(":", 1)[1]

    @property
    def evaluator_targets(self) -> tuple[str, ...]:
        """The tests the official evaluator runs: the recorded targets, else its command's path arguments."""
        def is_test(part: str) -> bool:
            return not part.startswith("-") and "=" not in part and (part.startswith("test") or ".py" in part)

        targets = tuple(part for part in self.test_targets if is_test(part))
        return targets or tuple(part for part in self.evaluator_command if is_test(part))

    @property
    def probes(self) -> tuple[str, ...]:
        return tuple(sorted({probe for point in self.dependency_points for probe in point.integrated_probes}))

    @property
    def executable_points(self) -> tuple[DependencyPoint, ...]:
        return tuple(point for point in self.dependency_points if point.executable)

    def assignment(self, agent_id: str) -> Assignment:
        for item in self.assignments:
            if item.agent_id == agent_id:
                return item
        raise KeyError(agent_id)


@dataclass
class Registry:
    root: Path
    commit: str
    pinned: bool
    tasks: dict[str, TaskRecord]
    verified_files: dict[str, str]
    official_task_ids: tuple[str, ...]
    execution_profile: dict[str, str]

    def get(self, name: str) -> TaskRecord:
        key = name if ":" in name else f"asyncodebench:{name}"
        if key not in self.tasks:
            raise KeyError(f"unknown task {name}")
        return self.tasks[key]

    def identity(self) -> dict[str, Any]:
        """What a frozen run records about the registry it used."""
        return {
            "root": str(self.root),
            "commit": self.commit,
            "pinned_commit": PINNED_COMMIT,
            "pinned": self.pinned,
            "release": RELEASE,
            "verified_files": len(self.verified_files),
            "execution_profile": self.execution_profile,
        }


def _git_head(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _verify(root: Path, relative: str, expected: str, verified: dict[str, str]) -> Path:
    path = root / relative
    if not path.exists():
        raise RegistryError(f"{relative} listed in the release is missing")
    actual = sha256_file(path)
    if actual != expected:
        raise RegistryError(f"{relative}: sha256 {actual} does not match the release record {expected}")
    verified[relative] = actual
    return path


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


def load_registry(root: str | Path, *, allow_unpinned: bool = False) -> Registry:
    """Load and verify the frozen release. An unpinned checkout is refused unless allowed (and recorded)."""
    base = Path(root).expanduser().resolve()
    commit = _git_head(base)
    pinned = commit == PINNED_COMMIT
    if not pinned and not allow_unpinned:
        raise RegistryError(f"{base} is at {commit or 'an unknown commit'}, expected {PINNED_COMMIT}")
    release = base / "manifests" / "release" / RELEASE
    official = _load(release / "official_tasks.json")
    index = _load(release / "task_index.json")
    verified: dict[str, str] = {}
    profile = official["execution_profile"]
    _verify(base, profile["path"], profile["sha256"], verified)
    baselines = official["validated_baseline_registry"]
    _verify(base, baselines["path"], baselines["sha256"], verified)

    origins = {}
    for name in ("commit0_repositories_full.v0.3.json", "commit0_repositories.v0.3.json"):
        path = base / "configs" / "tasks" / name
        if path.exists():
            for item in _load(path).get("repositories", []):
                origins.setdefault(item["name"], item["origin_url"])
    images = {}
    image_path = base / "configs" / "environments" / "official_task_images.v0.4.json"
    if image_path.exists():
        for item in _load(image_path).get("records", []):
            images[item["task_id"]] = (item.get("image", ""), item.get("digest", ""))

    tasks: dict[str, TaskRecord] = {}
    for entry in index["tasks"]:
        artifacts = entry["artifacts"]
        loaded = {name: _load(_verify(base, item["path"], item["sha256"], verified)) for name, item in artifacts.items()}
        overlays = []
        for item in entry["source"].get("overlays", []):
            _verify(base, item["path"], item["sha256"], verified)
            overlays.append(Overlay(item["path"], item["sha256"]))
        task_record = loaded["task"]
        scenarios = loaded["scenarios"]["scenarios"]
        private = [item for item in scenarios if item["execution_mode"] == "async_private"]
        scenario = private[0] if private else scenarios[0]
        assignments = tuple(
            Assignment(
                item["agent_id"], item.get("role", ""), item.get("subproblem_id", ""),
                tuple(item.get("writable_paths", [])), tuple(item.get("primary_test_targets", [])),
            )
            for item in scenario["assignments"]
        )
        points = tuple(
            DependencyPoint(
                item["dependency_id"], item.get("dependency_type", ""),
                item.get("producer_agent", ""), item.get("consumer_agent", ""),
                tuple(item.get("producer_files", [])), tuple(item.get("consumer_files", [])),
                tuple(item.get("upstream_probe_tests", [])), tuple(item.get("downstream_probe_tests", [])),
                tuple(item.get("integrated_probe_tests", [])), item.get("contract_summary", ""),
            )
            for item in loaded.get("metrics", {}).get("dependency_points", [])
        )
        source = entry["source"]
        image, digest = images.get(entry["task_id"], ("", ""))
        tasks[entry["task_id"]] = TaskRecord(
            task_id=entry["task_id"],
            repository=entry["repository"],
            benchmark=source.get("benchmark", task_record.get("task_source", "")),
            origin_url=origins.get(entry["repository"], ""),
            base_sha=source.get("base_sha", task_record.get("upstream_version", "")),
            overlays=tuple(overlays),
            evaluator_command=tuple(task_record.get("evaluator_command", [])),
            test_targets=tuple(task_record.get("test_targets", [])),
            problem_statement=task_record.get("problem_statement", ""),
            official_result_eligible=bool(entry.get("official_result_eligible")),
            assignments=assignments,
            dependency_points=points,
            budgets={
                key: scenario.get(key)
                for key in ("token_budget_per_agent", "step_budget_per_agent", "test_budget_per_agent",
                            "wall_clock_budget_seconds")
            },
            image=image,
            image_digest=digest,
            artifacts={name: dict(item) for name, item in artifacts.items()},
        )
    return Registry(base, commit, pinned, tasks, verified, tuple(official["official_task_ids"]), dict(profile))


__all__ = [
    "Assignment", "DependencyPoint", "Overlay", "PINNED_COMMIT", "RELEASE", "Registry", "RegistryError",
    "TaskRecord", "load_registry", "sha256_file",
]
