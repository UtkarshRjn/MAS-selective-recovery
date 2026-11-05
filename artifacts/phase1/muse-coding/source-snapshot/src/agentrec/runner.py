"""Entry point and shared execution/recording helpers for the current experiment."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from .faults import FaultSpec, Injector
from .llm import LLM
from .provenance import Provenance
from .recovery import coarse_lineage, full_restart, suffix_recover
from .tasks import Catalog
from .workflow import RunContext, run_agents
from .world import World

RECOVER = {"restart": full_restart, "suffix": suffix_recover, "coarse": coarse_lineage}


def run_workflow(
    task: dict[str, Any], catalog: Catalog, llm: LLM, spec: FaultSpec | None = None, verify: bool = False, scenario: dict[str, str] | None = None
) -> tuple[World, Provenance, Injector, dict[str, Any]]:
    world = World(*catalog)
    if task.get("authorization_policy"):
        from .authorization import initialize_world
        initialize_world(world, task)
    injector = Injector(spec)
    ctx = RunContext(task, world, llm, injector=injector, verify=verify, scenario=scenario or {})
    before, t0 = llm.usage.copy(), time.perf_counter()
    run_agents(ctx)
    stats = {"usage": llm.usage.since(before), "wall_s": round(time.perf_counter() - t0, 3),
             "provenance_write_s": getattr(ctx.prov, "write_s", 0.0)}
    return world, ctx.prov, injector, stats


class Recorder:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        (out_dir / "traces").mkdir(parents=True, exist_ok=True)
        self.rows = (out_dir / "rows.jsonl").open("a")

    def row(self, **fields: Any) -> None:
        self.rows.write(json.dumps(fields) + "\n")
        self.rows.flush()

    def trace(self, name: str, prov: Provenance, world: World) -> None:
        data = {
            "provenance": prov.to_dict(),
            "world": {"bookings": world.bookings, "outbox": world.outbox, "files": world.files, "action_history": world.action_history},
        }
        (self.out_dir / "traces" / f"{name}.json").write_text(json.dumps(data, indent=1))

def main() -> None:
    from .mbpp import add_cli
    parser = argparse.ArgumentParser(prog="agentrec")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--llm-seed", type=int, default=0)
    add_cli(parser.add_subparsers(dest="cmd", required=True))
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
