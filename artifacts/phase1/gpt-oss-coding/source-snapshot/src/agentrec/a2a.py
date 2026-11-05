"""A2ASecBench protocol-security cases (Li et al., ICLR 2026).

The official score runs a local Agent2Agent lab and checks protocol events
(a dereferenced URI, an executed artifact, an unbounded delegation). This module
does not stand that lab up. It loads the released cases, keeps the oracle, and
records the untrusted peer input as a faulty artifact in the provenance graph.

Code: https://github.com/SaFo-Lab/A2ASecBench
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .provenance import Provenance
from .recovery import coarse_taint

DEFAULT_ROOT = Path("data/a2asec-upstream")
EXECUTABLE = ("asrf", "atsi", "co", "hotf")

AS_CASE = re.compile(
    r'case_id="(?P<id>[^"]+)"\s*,\s*task_prompt="(?P<task>[^"]+)"\s*,\s*'
    r"agentcard_paths=paths\s*,\s*benign_choice=(?P<choice>\d+)",
    re.S,
)


@dataclass(frozen=True)
class A2ACase:
    family: str
    split: str
    case_id: str
    task: str
    is_control: bool
    oracle: dict[str, Any] = field(default_factory=dict)
    untrusted: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _assign(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"{path} has no {name} assignment")


def load_as(root: Path) -> list[A2ACase]:
    cases = []
    for path in sorted((root / "attacks/instances/as").glob("as_*.py")):
        match = AS_CASE.search(path.read_text())
        if not match:
            raise ValueError(f"could not parse {path.name}")
        cases.append(A2ACase(
            family="AS",
            split="release",
            case_id=match.group("id"),
            task=match.group("task"),
            is_control=False,
            oracle={"benign_choice": int(match.group("choice"))},
            untrusted="spoofed AgentCards",
            meta={"n_cards": 10},
        ))
    return cases


def load_cc(root: Path, family: str) -> list[A2ACase]:
    directory = "cc_whitebox" if family == "CC-whitebox" else "cc_blackbox"
    rows = _assign(root / "attacks/instances" / directory / "__init__.py", "CASE_METADATA")
    cases = []
    for row in rows:
        consistent = bool(row.get("ground_truth_consistent"))
        cases.append(A2ACase(
            family=family,
            split="release",
            case_id=row["case_id"],
            task=row.get("gt_reason", ""),
            is_control=consistent,
            oracle={"consistent": consistent, "side_effect_type": row.get("side_effect_type", "")},
            untrusted="" if consistent else "backend behavior hidden from the AgentCard",
        ))
    return cases


def load_executable(root: Path) -> list[A2ACase]:
    cases = []
    for family in EXECUTABLE:
        for split in ("executable", "calibration"):
            path = root / "attacks/instances" / family / f"{split}.py"
            if not path.exists():
                continue
            for row in _assign(path, "CASE_METADATA"):
                public = row.get("public") or {}
                env = row.get("environment") or {}
                payload = row.get("sut_payload") or {}
                cases.append(A2ACase(
                    family=row.get("attack_id", family.upper()),
                    split=split,
                    case_id=row["case_id"],
                    task=public.get("task") or payload.get("prompt") or row.get("gt_reason", ""),
                    is_control=bool(row.get("is_control", False)),
                    oracle=dict(row.get("oracle") or {}),
                    untrusted=str(env.get("peer_message") or payload.get("prompt") or ""),
                ))
    return cases


def load(root: Path = DEFAULT_ROOT) -> list[A2ACase]:
    if not (root / "attacks").is_dir():
        raise FileNotFoundError(f"{root} is missing. Run `agentrec a2a download`.")
    return load_as(root) + load_cc(root, "CC-whitebox") + load_cc(root, "CC-blackbox") + load_executable(root)


def to_provenance(case: A2ACase) -> Provenance:
    """The coordinator reads the untrusted peer input. Controls are not faults."""
    prov = Provenance()
    faulty = not case.is_control and bool(case.untrusted)
    artifact = prov.add_artifact("untrusted", case.untrusted, faulty=faulty)
    step = prov.add_step("coordinator", "coordinator")
    prov.add_input(artifact, step)
    return prov


def taint_span(case: A2ACase) -> dict[str, Any]:
    prov = to_provenance(case)
    steps = prov.of_kind("step")
    tainted = [n for n in steps if n.id in coarse_taint(prov)]
    return {
        "case_id": case.case_id,
        "family": case.family,
        "is_control": case.is_control,
        "tainted": bool(tainted),
    }


def summarize(cases: list[A2ACase]) -> None:
    print(f"{len(cases)} A2ASecBench cases")
    groups: dict[tuple[str, str], list[A2ACase]] = {}
    for case in cases:
        groups.setdefault((case.family, case.split), []).append(case)
    for (family, split), group in sorted(groups.items()):
        controls = sum(1 for c in group if c.is_control)
        attacks = len(group) - controls
        print(f"  {family:14} {split:12} n={len(group):4d}  attacks={attacks:4d}  controls={controls:4d}")


def cmd_download(args: Any) -> None:
    import subprocess

    dest = Path(args.data)
    if (dest / "attacks").is_dir():
        print(f"already present: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/SaFo-Lab/A2ASecBench.git", str(dest)],
        check=True,
    )
    print(f"cloned A2ASecBench -> {dest}")


def cmd_summary(args: Any) -> None:
    summarize(load(Path(args.data)))
