"""Command-line entry point.

  agentrec clean        clean-run success rate (week 1 baseline)
  agentrec determinism  how often identical planner calls give different outputs
  agentrec experiment   faulty runs, then each recovery method, under identical fault schedules
"""

from __future__ import annotations

import argparse
import copy
import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from . import agents
from .checker import check, outcome_signature
from .faults import DEFAULT_FAULTS, FAULTS_BY_NAME, FaultSpec, Injector
from .llm import LLM, MockLLM, ServerLLM
from .provenance import Provenance
from .recovery import coarse_lineage, coarse_taint, full_restart
from .tasks import Catalog, make_task
from .a2a import cmd_download as cmd_a2a_download
from .a2a import cmd_summary as cmd_a2a_summary
from .tamas import cmd_download as cmd_tamas_download
from .tamas import cmd_summary as cmd_tamas_summary
from .who_when import cmd_attribute, cmd_download, cmd_summary
from .workflow import RunContext, TravelWorkflow
from .world import World

METHODS = ("restart", "coarse", "verify")


def make_llm(args: argparse.Namespace) -> LLM:
    if args.llm == "mock":
        return MockLLM()
    return ServerLLM(base_url=args.base_url, temperature=args.temperature, seed=args.llm_seed)


def run_workflow(
    task: dict[str, Any], catalog: Catalog, llm: LLM, spec: FaultSpec | None = None, verify: bool = False
) -> tuple[World, Provenance, Injector, dict[str, Any]]:
    world = World(*catalog)
    injector = Injector(spec)
    ctx = RunContext(task, world, llm, injector=injector, verify=verify)
    before, t0 = llm.usage.copy(), time.perf_counter()
    TravelWorkflow().run(ctx)
    stats = {"usage": llm.usage.since(before), "wall_s": round(time.perf_counter() - t0, 3)}
    return world, ctx.prov, injector, stats


def _tokens(usage: dict[str, float]) -> float:
    return usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)


def _step_tokens(prov: Provenance, step_ids: set[str]) -> float:
    return sum(_tokens(prov.nodes[s].data.get("usage", {})) for s in step_ids)


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
            "world": {"bookings": world.bookings, "outbox": world.outbox, "files": world.files},
        }
        (self.out_dir / "traces" / f"{name}.json").write_text(json.dumps(data, indent=1))


def _out_dir(args: argparse.Namespace, label: str) -> Path:
    return Path(args.out) / f"{label}-{args.llm}-{datetime.now():%Y%m%d-%H%M%S}"


def cmd_clean(args: argparse.Namespace) -> None:
    llm = make_llm(args)
    rec = Recorder(_out_dir(args, "clean"))
    wins = 0
    for seed in range(args.seed, args.seed + args.tasks):
        task, truth, catalog = make_task(seed)
        world, prov, _, stats = run_workflow(task, catalog, llm)
        result = check(world, truth)
        wins += result["success"]
        rec.row(seed=seed, fault=None, method="clean", **result, **stats)
        rec.trace(f"{seed}_clean", prov, world)
        print(f"seed={seed} success={result['success']} tokens={_tokens(stats['usage']):.0f} wall={stats['wall_s']}s")
    print(f"clean success: {wins}/{args.tasks}  -> {rec.out_dir}")


def cmd_determinism(args: argparse.Namespace) -> None:
    llm = make_llm(args)
    task, _, catalog = make_task(args.seed)
    world = World(*catalog)
    flights = MockLLM._best_flight(task, world.search_flights(task["origin"], task["dest"], task["date"]))
    hotels = MockLLM._best_hotel(task, world.search_hotels(task["dest"], task["min_rating"], task["max_price"]))
    findings = {
        "flights": {"flight_id": flights["id"], "price": flights["price"], "notes": ""},
        "hotels": {
            "hotel_id": hotels["id"], "nightly_price": hotels["nightly_price"],
            "rating": hotels["rating"], "notes": "",
        },
    }
    messages = agents.planner_messages(task, findings)
    outputs = Counter(agents.dumps(llm.chat("planner", messages)) for _ in range(args.repeats))
    print(f"{args.repeats} identical planner calls -> {len(outputs)} distinct outputs")
    for out, n in outputs.most_common():
        print(f"  x{n}: {out[:160]}{'...' if len(out) > 160 else ''}")


def cmd_experiment(args: argparse.Namespace) -> None:
    llm = make_llm(args)
    specs = DEFAULT_FAULTS if args.faults == "all" else [FAULTS_BY_NAME[n] for n in args.faults.split(",")]
    methods = METHODS if args.methods == "all" else tuple(args.methods.split(","))
    rec = Recorder(_out_dir(args, "experiment"))
    print(f"writing to {rec.out_dir}")

    for seed in range(args.seed, args.seed + args.tasks):
        task, truth, catalog = make_task(seed)
        world, prov, _, stats = run_workflow(task, catalog, llm)
        clean = check(world, truth)
        clean_sig = outcome_signature(clean)
        rec.row(seed=seed, fault=None, method="clean", **clean, **stats)
        rec.trace(f"{seed}_clean", prov, world)
        print(f"seed={seed} clean success={clean['success']}")

        for spec in specs:
            base = {"seed": seed, "fault": spec.name, "adversarial": spec.adversarial, "clean_success": clean["success"]}
            world_f, prov_f, inj, stats_f = run_workflow(task, catalog, llm, spec)
            faulty = check(world_f, truth)
            taint = coarse_taint(prov_f)
            tainted_steps = {s.id for s in prov_f.of_kind("step") if s.id in taint}
            base.update(fault_fired=inj.fired > 0, fault_used=outcome_signature(faulty) != clean_sig)
            rec.row(
                **base, method="none", **faulty, **stats_f,
                steps_total=len(prov_f.of_kind("step")),
                tainted_steps=sorted(prov_f.nodes[s].key for s in tainted_steps),
                wasted_tokens=_step_tokens(prov_f, tainted_steps),
            )
            rec.trace(f"{seed}_{spec.name}_none", prov_f, world_f)
            line = f"  {spec.name:40s} fired={inj.fired > 0} used={base['fault_used']} success={faulty['success']}"
            if not base["fault_fired"]:
                print(line + "  (fault did not fire; skipping recovery)")
                continue

            results = []
            for method in methods:
                if method == "verify":
                    world_v, prov_v, _, stats_v = run_workflow(task, catalog, llm, spec, verify=True)
                    result = check(world_v, truth)
                    rec.row(**base, method="verify", **result, **stats_v, total_usage=stats_v["usage"])
                    rec.trace(f"{seed}_{spec.name}_verify", prov_v, world_v)
                else:
                    world_r, prov_r = copy.deepcopy(world_f), copy.deepcopy(prov_f)
                    recover = full_restart if method == "restart" else coarse_lineage
                    report = recover(task, world_r, llm, prov_r)
                    result = check(world_r, truth)
                    total = {k: stats_f["usage"][k] + report.usage[k] for k in report.usage}
                    rec.row(**base, method=method, **result, **report.row(), total_usage=total)
                    rec.trace(f"{seed}_{spec.name}_{method}", report.prov, world_r)
                results.append(f"{method}={result['success']}")
            print(line + "  " + " ".join(results))
    print(f"done -> {rec.out_dir}")


def main() -> None:
    p = argparse.ArgumentParser(prog="agentrec")
    p.add_argument("--llm", choices=("server", "mock"), default="server")
    p.add_argument("--base-url", default="http://localhost:8080/v1")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--llm-seed", type=int, default=0)
    p.add_argument("--seed", type=int, default=0, help="first task seed")
    p.add_argument("--out", default="results")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clean")
    c.add_argument("--tasks", type=int, default=10)
    c.set_defaults(fn=cmd_clean)

    d = sub.add_parser("determinism")
    d.add_argument("--repeats", type=int, default=10)
    d.set_defaults(fn=cmd_determinism)

    e = sub.add_parser("experiment")
    e.add_argument("--tasks", type=int, default=3)
    e.add_argument("--faults", default="all", help="'all' or comma-separated, e.g. timeout@research_flights")
    e.add_argument("--methods", default="all", help=f"'all' or comma-separated subset of {METHODS}")
    e.set_defaults(fn=cmd_experiment)

    w = sub.add_parser("who-when", help="Who&When failure-attribution logs")
    w.add_argument("--data", default="data/who-and-when")
    wsub = w.add_subparsers(dest="ww_cmd", required=True)
    dl = wsub.add_parser("download", help="fetch the official JSON logs from Hugging Face")
    dl.add_argument("--subset", choices=("algorithm", "handcrafted", "both"), default="both")
    dl.set_defaults(fn=cmd_download)
    sm = wsub.add_parser("summary", help="decisive-step position and coarse-lineage suffix")
    sm.add_argument("--subset", choices=("algorithm", "handcrafted", "both"), default="both")
    sm.set_defaults(fn=cmd_summary)
    at = wsub.add_parser("attribute", help="run an attribution prompt from Zhang et al. 2025")
    at.add_argument("--method", choices=("all_at_once", "step_by_step", "binary_search"), default="all_at_once")
    at.add_argument("--subset", choices=("algorithm", "handcrafted", "both"), default="algorithm")
    at.add_argument("--limit", type=int, default=3)
    at.add_argument("--ids", nargs="*", default=None, help="file ids to run, e.g. 1 12")
    at.add_argument("--answer", action=argparse.BooleanOptionalAction, default=True,
                    help="put the dataset answer in the prompt (the paper's with-ground-truth setting)")
    at.set_defaults(fn=cmd_attribute)

    t = sub.add_parser("tamas", help="TAMAS adversarial multi-agent scenarios")
    t.add_argument("--data", default="data/tamas-upstream")
    tsub = t.add_subparsers(dest="tamas_cmd", required=True)
    td = tsub.add_parser("download")
    td.set_defaults(fn=cmd_tamas_download)
    ts = tsub.add_parser("summary")
    ts.set_defaults(fn=cmd_tamas_summary)

    a = sub.add_parser("a2a", help="A2ASecBench protocol-security cases")
    a.add_argument("--data", default="data/a2asec-upstream")
    asub = a.add_subparsers(dest="a2a_cmd", required=True)
    ad = asub.add_parser("download")
    ad.set_defaults(fn=cmd_a2a_download)
    asm = asub.add_parser("summary")
    asm.set_defaults(fn=cmd_a2a_summary)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
