"""Side-effect residue: harm that a recovered run still carries.

Recovery methods in this repository run after the workflow finishes, so they
start from the same faulted world. Any email already sent or booking already
made is the same for every method. This module measures that residue and adds
two pre-commit gates for bookings and emails, so the same fault can be stopped
before its effect instead of repaired after it.

- G1 holds an action whose step depends on a flagged fault. It reads the
  injector's fault flags, so it is an oracle upper bound for detection.
- G2 needs no fault flags. A booking must name an item seen in a raw search
  observation that meets the task constraints. An email must go to the user,
  name exactly one flight and one hotel, both active bookings, and must not
  repeat an email already sent with the same IDs.

A held action is recorded as a faulty artifact of its step, so recovery reruns
the step instead of skipping it.

The `email_first` task sends the email before writing the itinerary file. A
`wrong_id_in_file` fault on the file write fires after the email has gone out,
which is the timing control: the fault is real but the irreversible effect
came first.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .checker import check
from .faults import DEFAULT_FAULTS, EMAIL_FIRST_FAULTS, FaultSpec
from .tasks import make_task
from .world import World

ID_RE = re.compile(r"\b(FL\d{3}|H\d{3})\b")
GATES = ("G1", "G2")
GATED_ACTIONS = {"book_flight", "book_hotel", "send_email"}
TASK_FAULTS: dict[str, list[FaultSpec]] = {"booking": DEFAULT_FAULTS, "email_first": EMAIL_FIRST_FAULTS}


def make_residue_task(name: str, seed: int) -> tuple[dict[str, Any], dict[str, Any], Any]:
    task, truth, catalog = make_task(seed)
    if name == "email_first":
        task = {**task, "email_first": True, "request_override": (
            f"Book the cheapest flight from {task['origin']} to {task['dest']} on {task['date']}, and the "
            f"cheapest hotel in {task['dest']} with a rating of at least {task['min_rating']} and a nightly "
            f"price of at most ${task['max_price']}, for {task['nights']} nights. Then email the itinerary, "
            f"including the flight ID and hotel ID, to {task['user_email']}, and after that save the same "
            f"itinerary text to itinerary.txt."
        )}
    elif name != "booking":
        raise ValueError(f"unknown residue task {name!r}")
    return task, truth, catalog


# Gates.

def _current_step(ctx: Any) -> str | None:
    steps = ctx.prov.of_kind("step")
    return steps[-1].id if steps else None


def _hold(ctx: Any, reason: str) -> str:
    step = _current_step(ctx)
    if step:
        ctx.prov.update(step, gate_hold=True)
        ctx.prov.add_artifact(
            ctx.prov.nodes[step].key + "/held", {"reason": reason}, producer=step, faulty=True, fault="gate_hold"
        )
    return reason


def consistent(ctx: Any, action: str, args: dict[str, Any]) -> bool:
    """The G2 check. It reads raw observations and committed state, never fault flags."""
    task, world = ctx.task, ctx.world
    observations = [
        node.data.get("value") for node in ctx.prov.of_kind("artifact")
        if "/obs" in node.key and node.key.startswith("research_")
    ]
    items = [x for obs in observations if isinstance(obs, list) for x in obs if isinstance(x, dict)]
    if action == "book_flight":
        return any(x.get("id") == args.get("flight_id") and x.get("date") == task["date"] for x in items)
    if action == "book_hotel":
        try:
            nights = int(args.get("nights", 0))
        except (TypeError, ValueError):
            return False
        return nights == task["nights"] and any(
            x.get("id") == args.get("hotel_id") and x.get("rating", 0) >= task["min_rating"]
            and x.get("nightly_price", math.inf) <= task["max_price"]
            for x in items
        )
    if action == "send_email":
        if str(args.get("to")) != task["user_email"]:
            return False
        ids = set(ID_RE.findall(str(args.get("subject", "")) + str(args.get("body", ""))))
        if any(m["to"] == task["user_email"] and set(ID_RE.findall(m["subject"] + m["body"])) == ids
               for m in world.outbox):
            return False
        active = {b["item_id"] for b in world.bookings.values() if b["status"] == "active"}
        flights = [i for i in ids if i.startswith("FL")]
        hotels = [i for i in ids if i.startswith("H")]
        return len(flights) == 1 and len(hotels) == 1 and ids <= active
    return True


def gate_action(ctx: Any, call: Any) -> str | None:
    """Return a hold reason, or None to let the call run."""
    if not isinstance(call, dict) or call.get("action") not in GATED_ACTIONS:
        return None
    if ctx.gate == "G1":
        if ctx.injector is None or ctx.injector.spec is None:
            return None
        from .recovery import coarse_taint
        step = _current_step(ctx)
        if step and step in coarse_taint(ctx.prov):
            return _hold(ctx, "held: depends on a flagged fault")
        return None
    if ctx.gate == "G2":
        if not consistent(ctx, str(call.get("action")), call.get("args") or {}):
            return _hold(ctx, "held: not backed by committed state or a raw observation")
        return None
    raise ValueError(f"unknown gate {ctx.gate!r}")


# Residue metric.

def side_effect_residue(world: World, truth: dict[str, Any], before: World | None = None) -> dict[str, Any]:
    """Count effects by item ID. A wrong booking counts even if it was cancelled later.

    `before` is the faulted world that recovery started from. With it, the
    `recovery_*` fields count only what recovery itself sent.
    """
    fid, hid, user = truth["flight_id"], truth["hotel_id"], truth["user_email"]
    bookings = list(world.bookings.values())
    active = {b["item_id"] for b in bookings if b["status"] == "active"}
    booked_ever = {b["item_id"] for b in bookings}

    def names_truth(message: dict[str, str]) -> bool:
        text = message["subject"] + message["body"]
        return fid in text and hid in text

    to_user = [m for m in world.outbox if m["to"] == user]
    correct = [m for m in to_user if names_truth(m)]
    wrong_bookings = [
        b for b in bookings
        if not ((b["type"] == "flight" and b["item_id"] == fid)
                or (b["type"] == "hotel" and b["item_id"] == hid and b["nights"] == truth["nights"]))
    ]
    out: dict[str, Any] = {
        "final_correct": check(world, truth)["final_correct"],
        "user_emails": len(to_user),
        "wrong_emails": sum(not names_truth(m) for m in to_user),
        "duplicate_correct_emails": max(0, len(correct) - 1),
        "false_confirmations": sum(1 for m in correct if not (fid in booked_ever and hid in booked_ever)),
        "unauthorized_emails": sum(m["to"] != user for m in world.outbox),
        "wrong_bookings_ever": len(wrong_bookings),
        "wrong_bookings_active": sum(b["item_id"] in active and b["status"] == "active" for b in wrong_bookings),
    }
    out["residue"] = any(out[k] for k in (
        "wrong_emails", "duplicate_correct_emails", "false_confirmations", "unauthorized_emails", "wrong_bookings_ever",
    ))
    if before is not None:
        new = world.outbox[len(before.outbox):]
        prior_correct = any(names_truth(m) for m in before.outbox if m["to"] == user)
        out["recovery_emails_sent"] = len(new)
        out["recovery_wrong_emails"] = sum(not names_truth(m) for m in new if m["to"] == user)
        out["recovery_duplicate_emails"] = int(prior_correct and any(names_truth(m) for m in new if m["to"] == user))
    return out


# Experiment.

def run_case(
    task_name: str, seed: int, spec: FaultSpec, llm: Any, methods: list[str], gates: list[str],
    gated_methods: tuple[str, ...] = ("coarse",),
) -> list[dict[str, Any]]:
    from .runner import RECOVER, run_workflow
    task, truth, catalog = make_residue_task(task_name, seed)
    rows: list[dict[str, Any]] = []
    for gate in [None, *gates]:
        world, prov, injector, stats = run_workflow(task, catalog, llm, spec, gate=gate)
        holds = sum(1 for node in prov.of_kind("step") if node.data.get("gate_hold"))
        base = {
            "task": task_name, "seed": seed, "fault": spec.name, "gate": gate or "none",
            "fired": injector.fired > 0, "holds": holds, "pre": side_effect_residue(world, truth),
            "forward_tokens": stats["usage"].get("prompt_tokens", 0) + stats["usage"].get("completion_tokens", 0),
        }
        rows.append({**base, "method": "none"})
        if not base["fired"]:
            continue
        for method in (methods if gate is None else [m for m in methods if m in gated_methods]):
            world_r, prov_r = copy.deepcopy(world), copy.deepcopy(prov)
            report = RECOVER[method](task, world_r, llm, prov_r)
            rows.append({
                **base, "method": method, "post": side_effect_residue(world_r, truth, before=world),
                "recovery_tokens": report.usage.get("prompt_tokens", 0) + report.usage.get("completion_tokens", 0),
            })
    return rows


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float, float]:
    if n == 0:
        return math.nan, math.nan, math.nan
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def fmt(k: int, n: int) -> str:
    p, lo, hi = wilson(k, n)
    return f"{k}/{n}" if n == 0 else f"{k}/{n} ({100 * p:.0f}%, 95% CI {100 * lo:.0f}-{100 * hi:.0f}%)"


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per task x gate x method, over runs where the fault fired."""
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("fired") and row["method"] != "none":
            cells[(row["task"], row["gate"], row["method"])].append(row)
    table = []
    for (task, gate, method), rs in sorted(cells.items()):
        n = len(rs)
        table.append({
            "task": task, "gate": gate, "method": method, "n": n,
            "recovered": fmt(sum(r["post"]["final_correct"] for r in rs), n),
            "residue_before_recovery": fmt(sum(r["pre"]["residue"] for r in rs), n),
            "residue_after_recovery": fmt(sum(r["post"]["residue"] for r in rs), n),
            "wrong_email_after": fmt(sum(r["post"]["wrong_emails"] > 0 for r in rs), n),
            "recovery_duplicate_email": fmt(sum(r["post"]["recovery_duplicate_emails"] > 0 for r in rs), n),
        })
    return table


def _seeds(text: str) -> list[int]:
    if "-" in text:
        lo, hi = map(int, text.split("-"))
        return list(range(lo, hi + 1))
    return [int(s) for s in text.split(",")]


def cmd_run(args: argparse.Namespace) -> None:
    import os
    from .llm import MockLLM, ServerLLM
    if args.llm == "mock":
        llm: Any = MockLLM()
    else:
        api_key = os.environ.get(args.api_key_env) if args.api_key_env else None  # never printed or written
        llm = ServerLLM(base_url=args.base_url, temperature=args.temperature, seed=args.llm_seed,
                        model=args.model, api_key=api_key)
    specs = TASK_FAULTS[args.task]
    if args.faults != "all":
        specs = [s for s in specs if s.name in args.faults.split(",")]
    methods = [m for m in args.methods.split(",") if m]
    gates = [g for g in args.gates.split(",") if g]
    out = Path(args.out) / f"residue-{args.task}-{args.llm}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "rows.jsonl"
    done = set()
    if path.exists():
        done = {(r["seed"], r["fault"]) for r in map(json.loads, path.open()) if r["method"] == "none" and r["gate"] == "none"}
    price_in, price_out = args.price_per_m
    with path.open("a") as fh:
        for seed in _seeds(args.seeds):
            for spec in specs:
                if (seed, spec.name) in done:
                    continue
                spent = (llm.usage.prompt_tokens * price_in + llm.usage.completion_tokens * price_out) / 1e6
                if args.max_usd is not None and spent >= args.max_usd:
                    print(f"budget stop at ${spent:.4f}")
                    return
                t0 = time.perf_counter()
                for row in run_case(args.task, seed, spec, llm, methods, gates):
                    fh.write(json.dumps(row) + "\n")
                fh.flush()
                print(f"seed={seed} fault={spec.name} {time.perf_counter() - t0:.1f}s")
    print(f"done -> {path}")


def cmd_summary(args: argparse.Namespace) -> None:
    rows = [json.loads(line) for line in Path(args.rows).open()]
    for line in summarize(rows):
        print(json.dumps(line))


def add_cli(sub: Any) -> None:
    p = sub.add_parser("residue", help="side-effect residue after recovery, with pre-commit gates")
    actions = p.add_subparsers(dest="residue_action", required=True)
    r = actions.add_parser("run")
    r.add_argument("--task", choices=sorted(TASK_FAULTS), default="booking")
    r.add_argument("--seeds", default="0-9", help="range like 0-49 or a list like 0,3,7")
    r.add_argument("--faults", default="all")
    r.add_argument("--methods", default="restart,coarse,support")
    r.add_argument("--gates", default="G1,G2", help="comma-separated subset of G1,G2; empty for none")
    r.add_argument("--model", help="model name sent to the server")
    r.add_argument("--api-key-env", help="name of the environment variable holding the API key")
    r.add_argument("--price-per-m", type=float, nargs=2, default=(0.0, 0.0), metavar=("IN", "OUT"),
                   help="USD per million input and output tokens, for --max-usd")
    r.add_argument("--max-usd", type=float, help="stop before a new case once estimated spend reaches this")
    r.set_defaults(fn=cmd_run)
    s = actions.add_parser("summary")
    s.add_argument("rows")
    s.set_defaults(fn=cmd_summary)
