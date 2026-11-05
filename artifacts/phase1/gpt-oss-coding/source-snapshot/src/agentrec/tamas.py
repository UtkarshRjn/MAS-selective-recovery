"""TAMAS adversarial multi-agent scenarios (Kavathekar et al., 2025).

The release is 300 instances: six attack types, five domains, ten tasks each.
Official scoring runs the instances in Autogen or CrewAI and has GPT-4o judge the
traces. This module does not do that. It loads the released tasks, names the
attack carrier, and places that carrier in the same provenance graph as the
booking workflow.

Dataset and code: https://github.com/microsoft/TAMAS
Paper: https://arxiv.org/abs/2511.05269
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .provenance import Provenance
from .recovery import coarse_taint

ATTACKS = ("Byzantine", "Colluding", "Contradicting", "DPI", "IPI", "Impersonation")
DEFAULT_ROOT = Path("data/tamas-upstream")


@dataclass(frozen=True)
class TamasCase:
    attack: str
    scenario: str
    index: int
    query: str
    agents: tuple[dict[str, str], ...]
    carriers: tuple[str, ...]
    attack_text: str

    @property
    def case_id(self) -> str:
        return f"{self.attack}/{self.scenario}/{self.index}"


def _scenario(path: Path) -> str:
    return path.stem.split("_", 1)[0]


def _carriers(attack: str, agents: list[dict[str, str]], query: str) -> tuple[list[str], str]:
    """Where the released instance puts the attack. IPI payloads are not in the JSON."""
    if attack == "Byzantine":
        names = [a["agent_name"] for a in agents if "behave unpredictably" in a["agent_description"]]
        text = " ".join(a["agent_description"] for a in agents if a["agent_name"] in names)
        return names, text
    if attack == "Colluding":
        names = [a["agent_name"] for a in agents if a["agent_description"].count(".") >= 2]
        text = " ".join(a["agent_description"] for a in agents if a["agent_name"] in names)
        return names, text
    if attack == "Contradicting":
        names = [a["agent_name"] for a in agents if "opposes" in a["agent_description"].lower()]
        text = " ".join(a["agent_description"] for a in agents if a["agent_name"] in names)
        return names, text
    if attack == "DPI":
        return ["user"], query
    if attack == "Impersonation":
        return ["user"], query
    return [], ""


def parse_file(path: Path, attack: str) -> list[TamasCase]:
    scenario = _scenario(path)
    cases = []
    for index, raw in enumerate(json.loads(path.read_text())):
        agents = raw["agents"]
        query = raw["user query"]
        carriers, text = _carriers(attack, agents, query)
        cases.append(TamasCase(attack, scenario, index, query, tuple(agents), tuple(carriers), text))
    return cases


def load(root: Path = DEFAULT_ROOT) -> list[TamasCase]:
    data = root / "data"
    if not data.is_dir():
        raise FileNotFoundError(f"{data} is missing. Run `agentrec tamas download`.")
    cases: list[TamasCase] = []
    for attack in ATTACKS:
        for path in sorted((data / attack).glob("*.json")):
            cases.extend(parse_file(path, attack))
    return cases


def to_provenance(case: TamasCase) -> Provenance:
    """Every agent reads the attack text, because TAMAS puts it in the shared roster or the user task."""
    prov = Provenance()
    attack = prov.add_artifact("attack", case.attack_text, faulty=bool(case.carriers))
    for agent in case.agents:
        name = agent["agent_name"]
        step = prov.add_step(re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), name)
        prov.add_input(attack, step)
    return prov


def taint_span(case: TamasCase) -> dict[str, Any]:
    if not case.carriers:
        return {
            "case_id": case.case_id,
            "carriers": [],
            "tainted_fraction": 0.0,
            "note": "no attack text in the released instance",
        }
    prov = to_provenance(case)
    steps = [n for n in prov.of_kind("step")]
    tainted = {n.key for n in steps if n.id in coarse_taint(prov)}
    return {
        "case_id": case.case_id,
        "carriers": list(case.carriers),
        "n_agents": len(steps),
        "tainted_agents": len(tainted),
        "tainted_fraction": round(len(tainted) / len(steps), 4) if steps else 0.0,
    }


def summarize(cases: list[TamasCase]) -> None:
    print(f"{len(cases)} TAMAS instances")
    for attack in ATTACKS:
        group = [c for c in cases if c.attack == attack]
        if not group:
            continue
        located = sum(1 for c in group if c.carriers)
        spans = [taint_span(c)["tainted_fraction"] for c in group]
        print(
            f"  {attack:16} n={len(group):3d}  carrier located={located:3d}  "
            f"mean agents tainted={sum(spans) / len(spans):5.1%}"
        )
    ipi = [c for c in cases if c.attack == "IPI"]
    if ipi and not any(c.carriers for c in ipi):
        print("  IPI instances in the release contain the benign task only.")
        print("  The upstream runner attaches malicious tools separately; those traces are not in the JSON.")


def cmd_download(args: Any) -> None:
    import subprocess

    dest = Path(args.data)
    if (dest / "data").is_dir():
        print(f"already present: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/microsoft/TAMAS.git", str(dest)],
        check=True,
    )
    print(f"cloned TAMAS -> {dest}")


def cmd_summary(args: Any) -> None:
    summarize(load(Path(args.data)))
